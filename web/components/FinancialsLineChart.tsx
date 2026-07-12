"use client";

import { ColorType, CrosshairMode, createChart, LineSeries } from "lightweight-charts";
import type { IChartApi, LineData, Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ChartRange } from "@/components/CandleChart";
import { tsToDate } from "@/lib/chartTime";
import type { FinancialPeriod } from "@/lib/types";

import styles from "./FinancialsLineChart.module.css";

const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";

// 분기말(YYYY.MM) → 해당 분기말 일자(YYYY-MM-DD). 캔들과 같은 일자 시간축에 점을 얹는다.
const QUARTER_END_DAY: Record<string, string> = { "03": "-03-31", "06": "-06-30", "09": "-09-30", "12": "-12-31" };

function periodToDate(period: string): string | null {
  const m = period.match(/(\d{4})\.(\d{2})/);
  if (!m) {
    return null;
  }
  const tail = QUARTER_END_DAY[m[2]];
  return tail ? `${m[1]}${tail}` : null;
}

// 토글 가능한 지표(억원/원/배/% 혼재라 지표별 별도 price scale). key=FinancialPeriod 필드.
interface Metric {
  key: keyof FinancialPeriod;
  label: string;
  color: string;
}
const METRICS: Metric[] = [
  { key: "operating_income", label: "영업이익", color: "#128a4d" },
  { key: "revenue", label: "매출", color: "#2b6cc0" },
  { key: "net_income", label: "당기순이익", color: "#eb6834" },
  { key: "ev_ebitda", label: "EV/EBITDA", color: "#8b5cf6" },
  { key: "roe", label: "ROE", color: "#d4a017" },
  { key: "eps", label: "EPS", color: "#7b4b2a" },
];

interface Props {
  data: FinancialPeriod[];
  range?: ChartRange | null;
  onRangeChange?: (from: string, to: string) => void; // 차트 조작 시 공유 구간 갱신
}

// 켜진 지표 수에 따른 차트 높이. 지표마다 독립 스케일이라 여러 개 켜면 라인·눈금이 겹쳐
// 고정 높이(260)에선 다 안 보인다 → 1개 초과분마다 늘려 모든 지표가 드러나게(상한 480).
const BASE_HEIGHT = 260;
const PER_METRIC = 44;
const MAX_HEIGHT = 480;
function autoHeight(activeCount: number): number {
  return Math.min(MAX_HEIGHT, BASE_HEIGHT + Math.max(0, activeCount - 1) * PER_METRIC);
}

// 재무 지표 라인차트 — 캔들과 동일한 lightweight-charts 시간축 + date-range 공유.
// 6개 지표를 토글로 on/off, 기본은 영업이익만. height prop 은 하위호환 무시(켜진 수로 자동 산정).
export default function FinancialsLineChart({ data, range = null, onRangeChange }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<Set<string>>(new Set(["operating_income"]));
  // 차트 재사용 + 프로그램적 구간설정 억제창. CandleChart 와 동일 패턴(재생성·루프 방지).
  const chartRef = useRef<IChartApi | null>(null);
  const suppressUntilRef = useRef(0);
  const SUPPRESS_MS = 250;
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;

  // period → 일자 매핑 + 오름차순 정렬(시간축 요구). 지표별 라인 데이터 미리 계산.
  const seriesData = useMemo(() => {
    const withDate = data
      .map((d) => ({ d, date: periodToDate(d.period) }))
      .filter((x): x is { d: FinancialPeriod; date: string } => x.date !== null)
      .sort((a, b) => a.date.localeCompare(b.date));
    const out: Record<string, LineData[]> = {};
    for (const m of METRICS) {
      out[m.key] = withDate
        .filter((x) => x.d[m.key] !== null && x.d[m.key] !== undefined)
        .map((x) => ({ time: x.date as Time, value: x.d[m.key] as number }));
    }
    return out;
  }, [data]);

  // 실제로 그려지는(켜졌고 데이터 있는) 지표 수로 높이 산정 — 켜기만 하고 데이터 없는 칩은 제외.
  const renderedCount = METRICS.filter(
    (m) => active.has(m.key) && seriesData[m.key]?.length > 0,
  ).length;
  const chartHeight = autoHeight(renderedCount);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: COLOR_TEXT, fontFamily: "inherit" },
      grid: { vertLines: { color: COLOR_GRID }, horzLines: { color: COLOR_GRID } },
      rightPriceScale: { borderColor: COLOR_GRID },
      timeScale: { borderColor: COLOR_GRID },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR" },
    });

    let any = false;
    for (const m of METRICS) {
      if (!active.has(m.key) || seriesData[m.key].length === 0) {
        continue;
      }
      const line = chart.addSeries(LineSeries, {
        color: m.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        // 단위가 제각각이라 지표마다 독립 스케일(왼쪽 숨김)로 형태만 비교.
        priceScaleId: m.key,
      });
      chart.priceScale(m.key).applyOptions({ visible: false });
      line.setData(seriesData[m.key]);
      any = true;
    }

    // 초기 구간(억제창 세움). 이후 range 변경은 아래 별도 effect 가 재생성 없이 처리.
    if (any && range) {
      suppressUntilRef.current = Date.now() + SUPPRESS_MS;
      try {
        chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
      } catch {
        chart.timeScale().fitContent();
      }
    } else if (any) {
      chart.timeScale().fitContent();
    }

    const onVisibleRangeChange = (r: { from: Time; to: Time } | null) => {
      if (Date.now() < suppressUntilRef.current) {
        return;
      }
      if (!r || !onRangeChangeRef.current) {
        return;
      }
      onRangeChangeRef.current(tsToDate(r.from), tsToDate(r.to));
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(onVisibleRangeChange);
    chartRef.current = chart;

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(onVisibleRangeChange);
      chart.remove();
      chartRef.current = null;
    };
    // range 의도적 제외(아래 별도 effect 가 재생성 없이 반영). CandleChart 와 동일.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesData, active]);

  // range 변경만 반영(차트 재생성 없이).
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !range) {
      return;
    }
    suppressUntilRef.current = Date.now() + SUPPRESS_MS;
    try {
      chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
    } catch {
      /* 범위가 데이터 밖이면 무시 */
    }
  }, [range]);

  const toggle = (key: string) =>
    setActive((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });

  return (
    <div className={styles.wrap}>
      <div ref={containerRef} className={styles.chart} style={{ height: chartHeight }} />
      <div className={styles.filters} role="group" aria-label="재무 지표 필터">
        {METRICS.map((m) => {
          const on = active.has(m.key);
          const has = seriesData[m.key]?.length > 0;
          return (
            <button
              key={m.key}
              type="button"
              className={on ? `${styles.chip} ${styles.chipOn}` : styles.chip}
              style={on ? { borderColor: m.color, color: m.color } : undefined}
              onClick={() => toggle(m.key)}
              disabled={!has}
              aria-pressed={on}
              title={has ? undefined : "데이터 없음"}
            >
              <span className={styles.dot} style={{ background: m.color }} />
              {m.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
