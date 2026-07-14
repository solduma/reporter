"use client";

import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  createSeriesMarkers,
  CrosshairMode,
  createChart,
  HistogramSeries,
  LineSeries,
} from "lightweight-charts";
import type {
  CandlestickData,
  HistogramData,
  IChartApi,
  LineData,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { tsToDate } from "@/lib/chartTime";
import type { CandlePoint, ElliottView, Timeframe } from "@/lib/types";

import styles from "./CandleChart.module.css";
import { StageBands } from "./stageBands";
import type { StageBand } from "./stageBands";
import { TimeDividers } from "./timeDividers";

// 한국 시장 관례: 상승 빨강 · 하락 파랑. globals.css 토큰을 SVG/캔버스가 해석하지 못해 직접 값을 둔다.
const COLOR_UP = "#c02b2b";
const COLOR_DOWN = "#2b6cc0";
const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";
// 이동평균선(10·20·120·150). 레전드를 차트 밖(컨트롤 바)에서도 렌더할 수 있게 export.
// MA150 은 와인스타인 30주 이동평균 등가선 — 국면 판정의 기준선이라 함께 표시한다.
export const MA_DEFS: { period: number; color: string }[] = [
  { period: 10, color: "#e8a33d" },
  { period: 20, color: "#2ca089" },
  { period: 120, color: "#8b5cf6" },
  { period: 150, color: "#d946a0" },
];

// 차트에 표시할 시간 범위(초 단위 UTC timestamp 또는 날짜 문자열). 지정 시 fitContent 대신
// setVisibleRange 로 이 구간만 보여준다. date-range 슬라이더가 제어한다.
export interface ChartRange {
  from: Time;
  to: Time;
}

interface Props {
  data: CandlePoint[];
  timeframe: Timeframe;
  height?: number; // 그리드용 소형 차트를 위해 컨테이너 높이를 조절(기본 420)
  range?: ChartRange | null; // 표시 구간(없으면 전체 fitContent)
  showControls?: boolean; // MA 레전드·로그 토글 표시(기본 true). 컨트롤을 밖으로 뺄 땐 false.
  // 사용자가 스크롤·드래그로 표시 구간을 바꾸면 그 시작·끝 일자(YYYY-MM-DD)를 알린다.
  // 여러 차트를 한 date-range 로 묶을 때 페이지가 이 콜백으로 공유 구간을 갱신한다.
  onRangeChange?: (from: string, to: string) => void;
  stageBands?: StageBand[]; // 와인스타인 국면 배경밴드(일봉 전용). 없으면 미표시.
  elliott?: ElliottView | null; // 엘리엇 파동 추정(일봉 전용). 피벗 라인 + 라벨 마커.
}

// 30분봉의 t는 타임존 없는 벽시계 시각이라, UTC로 간주해 표기 시각이 그대로 보이도록 한다.
function toChartTime(point: CandlePoint, tf: Timeframe): Time {
  if (tf === "30m") {
    return (Date.parse(`${point.t}Z`) / 1000) as UTCTimestamp;
  }
  return point.t.slice(0, 10);
}

// 종가 단순이동평균. 구간 미달 구간은 건너뛴다(선이 그 시점부터 시작).
function movingAverage(data: CandlePoint[], tf: Timeframe, period: number): LineData[] {
  const out: LineData[] = [];
  let sum = 0;
  for (let i = 0; i < data.length; i += 1) {
    sum += data[i].c;
    if (i >= period) {
      sum -= data[i - period].c;
    }
    if (i >= period - 1) {
      out.push({ time: toChartTime(data[i], tf), value: sum / period });
    }
  }
  return out;
}

// 시간 구분 경계: 각 봉의 구분 키가 직전 봉과 달라지는 첫 봉이 경계다.
// 30분봉=일(날짜), 일봉=월(연-월), 주봉=연. 그 첫 봉의 time 을 수직선 위치로 쓴다.
function dividerKey(point: CandlePoint, tf: Timeframe): string {
  const d = point.t.slice(0, 10); // YYYY-MM-DD
  if (tf === "30m") {
    return d; // 날짜가 바뀌면 새 일(日)
  }
  if (tf === "week") {
    return d.slice(0, 4); // 연
  }
  return d.slice(0, 7); // 월(day 및 기타)
}

function dividerTimes(data: CandlePoint[], tf: Timeframe): Time[] {
  const times: Time[] = [];
  let prevKey: string | null = null;
  for (const point of data) {
    const key = dividerKey(point, tf);
    if (prevKey !== null && key !== prevKey) {
      times.push(toChartTime(point, tf));
    }
    prevKey = key;
  }
  return times;
}

export default function CandleChart({
  data,
  timeframe,
  height = 420,
  range = null,
  showControls = true,
  onRangeChange,
  stageBands,
  elliott,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [logScale, setLogScale] = useState(false);
  // 차트 인스턴스를 ref 로 보관해 range 변경 시 재생성 없이 재사용한다(재생성=화면 움찔거림 원인).
  const chartRef = useRef<IChartApi | null>(null);
  // 프로그램적 setVisibleRange 가 유발하는 이벤트를 억제하는 시간창(ms 타임스탬프). 값 비교는
  // 데이터 밀도별 스냅 차이(캔들=일·재무=분기)로 실패하고, 카운터는 이벤트가 0건이면 누수되므로,
  // "방금 내가 설정했다" 직후 짧은 창 동안의 이벤트를 모두 삼켜 피드백 루프를 끊는다.
  const suppressUntilRef = useRef(0);
  const SUPPRESS_MS = 250;
  // 드래그 중 이벤트가 프레임마다 쏟아져 상위 리렌더가 폭주하는 걸 막는다: rAF 로 한 프레임에 한 번만
  // emit. 또 이 차트가 마지막으로 내보낸 구간을 기억해, 그게 range 로 되돌아오면 재적용을 건너뛴다
  // (드래그 중인 차트는 이미 그 위치라 setVisibleRange 재호출이 곧 버벅임).
  const emitRafRef = useRef(0);
  const pendingRef = useRef<{ from: string; to: string } | null>(null);
  const lastEmittedRef = useRef<{ from: string; to: string } | null>(null);
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: COLOR_TEXT,
        fontFamily: "inherit",
      },
      grid: {
        vertLines: { color: COLOR_GRID },
        horzLines: { color: COLOR_GRID },
      },
      rightPriceScale: {
        borderColor: COLOR_GRID,
        mode: logScale ? 1 : 0, // 1 = Logarithmic
      },
      timeScale: {
        borderColor: COLOR_GRID,
        timeVisible: timeframe === "30m",
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR" },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLOR_UP,
      downColor: COLOR_DOWN,
      wickUpColor: COLOR_UP,
      wickDownColor: COLOR_DOWN,
      borderVisible: false,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const candles: CandlestickData[] = data.map((point) => ({
      time: toChartTime(point, timeframe),
      open: point.o,
      high: point.h,
      low: point.low,
      close: point.c,
    }));
    const volumes: HistogramData[] = data.map((point) => ({
      time: toChartTime(point, timeframe),
      value: point.v,
      color: point.c >= point.o ? COLOR_UP : COLOR_DOWN,
    }));

    candleSeries.setData(candles);
    volumeSeries.setData(volumes);

    // 이동평균선 오버레이(데이터가 충분한 기간만).
    for (const { period, color } of MA_DEFS) {
      if (data.length < period) {
        continue;
      }
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(movingAverage(data, timeframe, period));
    }

    // 와인스타인 국면 배경밴드(있으면). 캔들 뒤에 칠해 국면 구간을 표시.
    // 밴드 경계는 일봉 날짜라 주봉·분봉 축엔 정확히 없다 → 축 캔들 시각(epoch 초)을 넘겨
    // 가까운 봉으로 스냅해 칠하게 한다(일봉이 아니어도 오버레이 적용).
    if (stageBands && stageBands.length > 0) {
      const axisEpochs = data.map((p) =>
        timeframe === "30m" ? Date.parse(`${p.t}Z`) / 1000 : Date.parse(`${p.t.slice(0, 10)}T00:00:00Z`) / 1000,
      );
      candleSeries.attachPrimitive(
        new StageBands(
          chart,
          stageBands.map((b) => ({ stage: b.stage, from: b.from, to: b.to })),
          axisEpochs,
        ),
      );
    }

    // 엘리엇 파동 — 전 구간 상승 추진↔하락 조정이 중단없이 연결된 파동 체인. 한붓그리기(연결 폴리라인)
    // 로 그려 상승5파↔하락3파 톱니 구조가 드러나게 하고, 흰색 헤일로 + 진한 남색 선으로 MA(주황·틸·
    // 보라·자홍)·캔들과 확실히 분리한다. 파동 경계(1~5·A~C)와 하위 프랙탈 라벨을 마커로.
    // 엘리엇 파동 — 반복 사이클 1-2-3-4-5(추진)·A-B-C(조정). 각 다리(세그먼트)가 한 파동이고 그
    // wave_label(1~5·A~C)을 끝점에 표시한다. 사이클 다리를 이어 흰 헤일로 + 진남색 선으로 그려
    // MA·캔들과 분리. 추진=파랑 라벨(위), 조정=빨강 라벨(아래).
    const segs = elliott?.segments ?? [];
    if (elliott && segs.length >= 1) {
      const markers: SeriesMarker<Time>[] = [];
      // 파동 다리를 갭 없이 이어 그린다(추진 5파↔조정 3파가 끊김 없이 연결). 각 다리는 신뢰도로
      // 차등: 하드룰+피보 통과(고신뢰)는 진한 실선 + 라벨, 연결용(저신뢰)은 옅은 점선 + 라벨 생략
      // → 사용자가 "진짜 카운트"와 "연결용 추정"을 한눈에 구분. 흰 헤일로로 MA·캔들과 분리.
      const CONF_HI = 0.4; // 이 미만은 연결용(저신뢰) — 억지 카운트 아님을 옅게 표시
      for (const seg of segs) {
        const strong = (seg.confidence ?? 0) >= CONF_HI;
        const pts = [
          { time: seg.start_date.slice(0, 10) as Time, value: seg.start_price },
          { time: seg.end_date.slice(0, 10) as Time, value: seg.end_price },
        ];
        if (strong) {
          const halo = chart.addSeries(LineSeries, {
            color: "rgba(255,255,255,0.9)", lineWidth: 4,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          halo.setData(pts);
        }
        // 추진=진남색, 조정=자홍(방향 아닌 위상으로 색 구분). 저신뢰는 옅은 톤 + 점선.
        const motive = seg.phase === "motive";
        const line = chart.addSeries(LineSeries, {
          color: strong
            ? (motive ? "#1e293b" : "#9333ea")
            : (motive ? "rgba(30,41,59,0.35)" : "rgba(147,51,234,0.35)"),
          lineWidth: strong ? 2 : 1,
          lineStyle: strong ? 0 : 2, // 0=실선, 2=점선(연결용)
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        line.setData(pts);
        // 파동 번호는 고신뢰 다리에만(저신뢰는 라벨 생략 — 차트가 번호로 뒤덮이지 않게).
        if (strong) {
          markers.push({
            time: seg.end_date.slice(0, 10) as Time,
            position: seg.direction === "up" ? "aboveBar" : "belowBar",
            shape: "circle",
            color: motive ? "#1d4ed8" : "#9333ea",
            text: seg.wave_label,
          });
        }
      }
      markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
      createSeriesMarkers(candleSeries, markers);

      // 다음 파동 목표 — 규모(가격 low~high) × 기간(now~now+예상봉수)을 미래 축까지 뻗는 2D zone 으로.
      // 상·하한을 현재봉→미래봉까지 청록 점선으로 그려 "언제쯤 어디까지"를 사각 영역으로 보인다.
      if (elliott.projection && data.length > 0 && timeframe !== "30m") {
        const { low, high, bars_low, bars_high, wave } = elliott.projection;
        const lastDate = data[data.length - 1].t.slice(0, 10);
        // 미래 목표 시점 = 마지막봉 + 예상 봉수(달력일 근사: 봉수×1.4 로 주말 보정).
        const future = new Date(`${lastDate}T00:00:00Z`);
        future.setUTCDate(future.getUTCDate() + Math.round((bars_high || 10) * 1.4));
        const futureDate = future.toISOString().slice(0, 10) as Time;
        const nowT = lastDate as Time;
        // 채움 밴드: BaselineSeries 의 baseValue=low, 라인=high → low~high 사이가 반투명 청록으로
        // 채워진 사각 zone(규모×기간). 상·하한 경계선은 그 위에 점선으로 덧그린다.
        const band = chart.addSeries(BaselineSeries, {
          baseValue: { type: "price", price: low },
          topFillColor1: "rgba(8,145,178,0.18)", topFillColor2: "rgba(8,145,178,0.18)",
          topLineColor: "rgba(8,145,178,0.85)", bottomLineColor: "rgba(8,145,178,0)",
          bottomFillColor1: "rgba(8,145,178,0)", bottomFillColor2: "rgba(8,145,178,0)",
          lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        band.setData([
          { time: nowT, value: high },
          { time: futureDate, value: high },
        ]);
        const lowLine = chart.addSeries(LineSeries, {
          color: "rgba(8,145,178,0.85)", lineWidth: 2, lineStyle: 2,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        lowLine.setData([
          { time: nowT, value: low },
          { time: futureDate, value: low },
        ]);
        const barTxt = bars_low && bars_high ? ` ${bars_low}~${bars_high}봉` : "";
        candleSeries.createPriceLine({
          price: high, color: "#0891b2", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `${wave}${barTxt}`,
        });
        candleSeries.createPriceLine({
          price: low, color: "#0891b2", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: "",
        });
      }
    }

    // 시간 구분 수직선(붉은 점선): 30분봉=일 · 일봉=월 · 주봉=연 경계.
    const dividers = dividerTimes(data, timeframe);
    if (dividers.length > 0) {
      candleSeries.attachPrimitive(new TimeDividers(chart, dividers));
    }

    // 초기 구간: range 있으면 그 구간(억제 표식 세움), 없으면 전체. 이후 range 변경은 아래 별도
    // effect 가 재생성 없이 처리한다(range 를 이 effect deps 에 넣으면 매 동기화마다 차트가
    // 파괴·재생성돼 화면이 움찔거림).
    if (range) {
      suppressUntilRef.current = Date.now() + SUPPRESS_MS;
      try {
        chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
      } catch {
        chart.timeScale().fitContent();
      }
    } else {
      chart.timeScale().fitContent();
    }

    // 사용자 스크롤·드래그로 구간이 바뀌면 알린다. 프로그램적 setVisibleRange 직후의 이벤트는
    // 억제창으로 삼키고(피드백 루프 차단), 나머지는 rAF 로 프레임당 1회만 상위에 보고(리렌더 폭주 방지).
    const onVisibleRangeChange = (r: { from: Time; to: Time } | null) => {
      if (Date.now() < suppressUntilRef.current || !r || !onRangeChangeRef.current) {
        return;
      }
      pendingRef.current = { from: tsToDate(r.from), to: tsToDate(r.to) };
      if (emitRafRef.current) {
        return;
      }
      emitRafRef.current = requestAnimationFrame(() => {
        emitRafRef.current = 0;
        const p = pendingRef.current;
        if (p && onRangeChangeRef.current) {
          lastEmittedRef.current = p;
          onRangeChangeRef.current(p.from, p.to);
        }
      });
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(onVisibleRangeChange);
    chartRef.current = chart;

    return () => {
      if (emitRafRef.current) {
        cancelAnimationFrame(emitRafRef.current);
        emitRafRef.current = 0;
      }
      chart.timeScale().unsubscribeVisibleTimeRangeChange(onVisibleRangeChange);
      chart.remove();
      chartRef.current = null;
    };
    // range 는 의도적으로 제외 — 아래 별도 effect 가 재생성 없이 반영한다(deps 에 넣으면 매 동기화마다
    // 차트가 파괴·재생성돼 움찔거림). 초기 range 는 최초 마운트 시 위에서 1회 적용된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, timeframe, logScale, stageBands, elliott]);

  // range 변경만 반영(차트 재생성 없이). 프로그램적 적용이라 직후 이벤트를 억제창으로 삼킨다.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !range) {
      return;
    }
    // 이 차트가 방금 내보낸 구간이 그대로 되돌아온 것이면 이미 그 위치라 재적용 불필요(자기 메아리).
    // 드래그 중인 차트에 setVisibleRange 를 다시 걸지 않아 버벅임을 없앤다.
    const last = lastEmittedRef.current;
    if (last && last.from === tsToDate(range.from) && last.to === tsToDate(range.to)) {
      return;
    }
    suppressUntilRef.current = Date.now() + SUPPRESS_MS;
    try {
      chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
    } catch {
      /* 범위가 데이터 밖이면 무시 */
    }
  }, [range]);

  return (
    <div className={styles.wrap}>
      {showControls ? (
        <div className={styles.controls}>
          <div className={styles.maLegend}>
            {MA_DEFS.map((m) => (
              <span key={m.period} className={styles.maItem}>
                <span className={styles.maDot} style={{ background: m.color }} />
                MA{m.period}
              </span>
            ))}
          </div>
          <button
            type="button"
            className={logScale ? `${styles.logBtn} ${styles.logBtnActive}` : styles.logBtn}
            onClick={() => setLogScale((v) => !v)}
            aria-pressed={logScale}
          >
            로그
          </button>
        </div>
      ) : null}
      <div ref={containerRef} className={styles.chart} style={{ height }} />
    </div>
  );
}
