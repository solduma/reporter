"use client";

import { AreaSeries, ColorType, createChart, LineSeries } from "lightweight-charts";
import type { IChartApi, LineData, Time } from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import type { ChartRange } from "@/components/CandleChart";
import { useChartRangeSync } from "@/lib/useChartRangeSync";
import type { FinancialPeriod } from "@/lib/types";

import styles from "./MultipleBandChart.module.css";

const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";
const COLOR_LINE = "#2b6cc0";
const COLOR_BAND_MID = "#eb6834"; // 중앙값(50%)
const COLOR_BAND_EDGE = "#c9a06b"; // 25/75% 분위
const COLOR_CHEAP_ZONE = "rgba(46, 160, 108, 0.10)"; // 하위 25%(저평가) 음영 — 연한 초록

const QUARTER_END_DAY: Record<string, string> = { "03": "-03-31", "06": "-06-30", "09": "-09-30", "12": "-12-31" };

function periodToDate(period: string): string | null {
  const m = period.match(/(\d{4})\.(\d{2})/);
  if (!m) {
    return null;
  }
  const tail = QUARTER_END_DAY[m[2]];
  return tail ? `${m[1]}${tail}` : null;
}

// 분위수(선형보간). p∈[0,1].
function quantile(sorted: number[], p: number): number {
  if (sorted.length === 1) {
    return sorted[0];
  }
  const idx = p * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

type MetricKey = "per" | "pbr" | "psr";
const METRICS: { key: MetricKey; label: string }[] = [
  { key: "per", label: "PER" },
  { key: "pbr", label: "PBR" },
  { key: "psr", label: "PSR" },
];

// 단일 멀티플의 시계열 라인 + 25/50/75% 분위수 밴드(수평선). 저평가/고평가 위치를 한눈에.
function BandChart({
  data,
  metric,
  range,
  height,
  onRangeChange,
}: {
  data: FinancialPeriod[];
  metric: MetricKey;
  range: ChartRange | null;
  height: number;
  onRangeChange?: (from: string, to: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // 차트 인스턴스를 ref 로 보관해 range 변경 시 재생성 없이 재사용(연동 동기화는 useChartRangeSync).
  const chartRef = useRef<IChartApi | null>(null);

  const { line, bands } = useMemo(() => {
    const pts = data
      .map((d) => ({ v: d[metric] as number | null | undefined, date: periodToDate(d.period) }))
      .filter(
        (x): x is { v: number; date: string } =>
          x.v !== null && x.v !== undefined && x.v > 0 && x.date !== null,
      )
      .sort((a, b) => a.date.localeCompare(b.date));
    const lineData: LineData[] = pts.map((x) => ({ time: x.date as Time, value: x.v }));
    if (pts.length < 2) {
      return { line: lineData, bands: null };
    }
    const sorted = pts.map((x) => x.v).sort((a, b) => a - b);
    return {
      line: lineData,
      bands: {
        q25: quantile(sorted, 0.25),
        q50: quantile(sorted, 0.5),
        q75: quantile(sorted, 0.75),
        first: pts[0].date as Time,
        last: pts[pts.length - 1].date as Time,
      },
    };
  }, [data, metric]);

  // 연동 동기화용 봉 epoch(초) 축 — 메인 라인의 분기말 시각(오름차순). 밴드 수평선은 첫·끝만 써서
  // 논리 인덱스 병합에 영향 없다(라인 시각의 부분집합).
  const epochs = useMemo(
    () => line.map((pt) => Date.parse(`${pt.time as string}T00:00:00Z`) / 1000),
    [line],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container || line.length === 0) {
      return;
    }
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: COLOR_TEXT, fontFamily: "inherit" },
      grid: { vertLines: { color: COLOR_GRID }, horzLines: { color: COLOR_GRID } },
      rightPriceScale: { borderColor: COLOR_GRID },
      timeScale: { borderColor: COLOR_GRID },
      localization: { locale: "ko-KR" },
    });

    // 분위수 밴드: 수평선(첫~마지막 시점 동일 값).
    if (bands) {
      // 하위 25%(저평가) 영역 음영 — q25 값의 area 는 축 바닥까지 채워져 '25% 이하' 구간을 덮는다.
      // 밴드 선보다 먼저 그려 선이 위에 오게 한다.
      const cheapZone = chart.addSeries(AreaSeries, {
        topColor: COLOR_CHEAP_ZONE,
        bottomColor: COLOR_CHEAP_ZONE,
        lineColor: "transparent",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      cheapZone.setData([
        { time: bands.first, value: bands.q25 },
        { time: bands.last, value: bands.q25 },
      ]);
      const bandDefs: { v: number; color: string; dash: boolean }[] = [
        { v: bands.q75, color: COLOR_BAND_EDGE, dash: true },
        { v: bands.q50, color: COLOR_BAND_MID, dash: false },
        { v: bands.q25, color: COLOR_BAND_EDGE, dash: true },
      ];
      for (const b of bandDefs) {
        const s = chart.addSeries(LineSeries, {
          color: b.color,
          lineWidth: 1,
          lineStyle: b.dash ? 2 : 0, // 2=dashed
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData([
          { time: bands.first, value: b.v },
          { time: bands.last, value: b.v },
        ]);
      }
    }

    const main = chart.addSeries(LineSeries, {
      color: COLOR_LINE,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    main.setData(line);

    // range 없으면 fitContent. range 있으면 아래 동기화 훅이 적용한다.
    if (!range) {
      chart.timeScale().fitContent();
    }
    chartRef.current = chart;

    return () => {
      chart.remove();
      chartRef.current = null;
    };
    // range 의도적 제외(아래 동기화 훅이 재생성 없이 반영). CandleChart 와 동일.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line, bands]);

  // 연동 차트 동기화(논리 범위 기반). 차트 재생성 조건(line·bands)과 deps 를 맞춘다.
  useChartRangeSync({
    getChart: () => chartRef.current,
    getEpochs: () => epochs,
    range,
    onRangeChange,
    deps: [line, bands],
  });

  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>
        {METRICS.find((m) => m.key === metric)?.label} (배)
        {bands ? (
          <span className={styles.bandInfo}>
            <span className={styles.cheapSwatch} aria-hidden />
            저평가 25%={bands.q25.toFixed(1)} · 中={bands.q50.toFixed(1)} · 75%={bands.q75.toFixed(1)}
          </span>
        ) : null}
      </figcaption>
      {line.length === 0 ? (
        <div className={styles.status} style={{ height }}>
          데이터 없음
        </div>
      ) : (
        <div ref={containerRef} className={styles.chart} style={{ height }} />
      )}
    </figure>
  );
}

interface Props {
  data: FinancialPeriod[];
  range?: ChartRange | null;
  height?: number;
  onRangeChange?: (from: string, to: string) => void; // 밴드 조작 시 공유 구간 갱신(3개 연동)
}

// PER·PBR·PSR 3분할 밴드 차트. 각 멀티플의 25/50/75% 분위수 밴드로 역사적 위치 비교.
// 셋이 같은 range 를 공유하고, 하나를 스크롤·드래그하면 onRangeChange 로 나머지도 함께 움직인다.
export default function MultipleBandChart({ data, range = null, height = 220, onRangeChange }: Props) {
  return (
    <div className={styles.row}>
      {METRICS.map((m) => (
        <BandChart
          key={m.key}
          data={data}
          metric={m.key}
          range={range}
          height={height}
          onRangeChange={onRangeChange}
        />
      ))}
    </div>
  );
}
