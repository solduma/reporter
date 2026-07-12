"use client";

import { ColorType, createChart, LineSeries } from "lightweight-charts";
import type { LineData, Time } from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import type { ChartRange } from "@/components/CandleChart";
import { tsToDate } from "@/lib/chartTime";
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
  onRangeChange,
}: {
  data: FinancialPeriod[];
  metric: MetricKey;
  range: ChartRange | null;
  height: number;
  onRangeChange?: (from: string, to: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // 프로그램적으로 설정한 마지막 구간(메아리 식별용). CandleChart 와 동일 패턴.
  const lastAppliedRef = useRef<{ from: string; to: string } | null>(null);
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;

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

    // 설정한 구간을 기록해 두고 이벤트가 이 값과 같으면(메아리) 콜백을 건너뛴다(CandleChart 와 동일).
    if (range) {
      lastAppliedRef.current = { from: tsToDate(range.from), to: tsToDate(range.to) };
      try {
        chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
      } catch {
        chart.timeScale().fitContent();
      }
    } else {
      lastAppliedRef.current = null;
      chart.timeScale().fitContent();
    }

    // 사용자 스크롤·드래그로 표시 구간이 바뀌면(메아리 제외) 시작·끝 일자를 알린다 → 3개 밴드 연동.
    const onVisibleRangeChange = (r: { from: Time; to: Time } | null) => {
      if (!r || !onRangeChangeRef.current) {
        return;
      }
      const from = tsToDate(r.from);
      const to = tsToDate(r.to);
      const last = lastAppliedRef.current;
      if (last && last.from === from && last.to === to) {
        return;
      }
      onRangeChangeRef.current(from, to);
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(onVisibleRangeChange);

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(onVisibleRangeChange);
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
