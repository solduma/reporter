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
import { useEffect, useMemo, useRef, useState } from "react";

import { useChartRangeSync } from "@/lib/useChartRangeSync";
import type { CandlePoint, Timeframe } from "@/lib/types";

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
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [logScale, setLogScale] = useState(false);
  // 차트 인스턴스를 ref 로 보관해 range 변경 시 재생성 없이 재사용한다(재생성=화면 움찔거림 원인).
  const chartRef = useRef<IChartApi | null>(null);

  // 봉 epoch(초) 오름차순 축 — 논리↔달력 변환의 기준(연동 차트 동기화용).
  const epochs = useMemo(
    () =>
      data.map((p) =>
        timeframe === "30m"
          ? Date.parse(`${p.t}Z`) / 1000
          : Date.parse(`${p.t.slice(0, 10)}T00:00:00Z`) / 1000,
      ),
    [data, timeframe],
  );

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
      candleSeries.attachPrimitive(
        new StageBands(
          chart,
          stageBands.map((b) => ({ stage: b.stage, from: b.from, to: b.to })),
          epochs,
        ),
      );
    }


    // 시간 구분 수직선(붉은 점선): 30분봉=일 · 일봉=월 · 주봉=연 경계.
    const dividers = dividerTimes(data, timeframe);
    if (dividers.length > 0) {
      candleSeries.attachPrimitive(new TimeDividers(chart, dividers));
    }

    chartRef.current = chart;

    return () => {
      chart.remove();
      chartRef.current = null;
    };
    // range 는 의도적으로 제외 — 아래 동기화 훅이 재생성 없이 반영한다(deps 에 넣으면 매 동기화마다
    // 차트가 파괴·재생성돼 움찔거림). 초기 range 는 훅이 마운트 시 적용한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, timeframe, logScale, stageBands]);

  // 연동 차트 동기화(논리 범위 기반 — 데이터 끝 너머 여백까지 전파). 차트 재생성 조건과 deps 를 맞춘다.
  useChartRangeSync({
    getChart: () => chartRef.current,
    getEpochs: () => epochs,
    range,
    onRangeChange,
    deps: [data, timeframe, logScale, stageBands],
  });

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
