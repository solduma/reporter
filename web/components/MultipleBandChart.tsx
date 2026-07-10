"use client";

import { ColorType, createChart, LineSeries } from "lightweight-charts";
import type { LineData, Time } from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import type { ChartRange } from "@/components/CandleChart";
import type { FinancialPeriod } from "@/lib/types";

import styles from "./MultipleBandChart.module.css";

const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";
const COLOR_LINE = "#2b6cc0";
const COLOR_BAND_MID = "#eb6834"; // 중앙값(50%)
const COLOR_BAND_EDGE = "#c9a06b"; // 25/75% 분위

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
}: {
  data: FinancialPeriod[];
  metric: MetricKey;
  range: ChartRange | null;
  height: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

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

    if (range) {
      try {
        chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
      } catch {
        chart.timeScale().fitContent();
      }
    } else {
      chart.timeScale().fitContent();
    }

    return () => {
      chart.remove();
    };
  }, [line, bands, range]);

  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>
        {METRICS.find((m) => m.key === metric)?.label} (배)
        {bands ? (
          <span className={styles.bandInfo}>
            25%={bands.q25.toFixed(1)} · 中={bands.q50.toFixed(1)} · 75%={bands.q75.toFixed(1)}
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
}

// PER·PBR·PSR 3분할 밴드 차트. 각 멀티플의 25/50/75% 분위수 밴드로 역사적 위치 비교.
export default function MultipleBandChart({ data, range = null, height = 220 }: Props) {
  return (
    <div className={styles.row}>
      {METRICS.map((m) => (
        <BandChart key={m.key} data={data} metric={m.key} range={range} height={height} />
      ))}
    </div>
  );
}
