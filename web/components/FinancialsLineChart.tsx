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

// 토글 가능한 지표. 단위(unit)가 같은 지표끼리는 한 축을 공유해 비교 가능하게 한다.
// unit: won=억원(매출·이익), krw=원(EPS), pct=%(ROE), mult=배(EV/EBITDA).
type Unit = "won" | "krw" | "pct" | "mult";
interface Metric {
  key: keyof FinancialPeriod;
  label: string;
  color: string;
  unit: Unit;
}
const METRICS: Metric[] = [
  { key: "operating_income", label: "영업이익", color: "#128a4d", unit: "won" },
  { key: "revenue", label: "매출", color: "#2b6cc0", unit: "won" },
  { key: "net_income", label: "당기순이익", color: "#eb6834", unit: "won" },
  { key: "ev_ebitda", label: "EV/EBITDA", color: "#8b5cf6", unit: "mult" },
  { key: "roe", label: "ROE", color: "#d4a017", unit: "pct" },
  { key: "eps", label: "EPS", color: "#7b4b2a", unit: "krw" },
];

// 축 눈금 포맷터(단위별). 재무 원값은 억원 단위 → 1만억(=1조) 이상은 '조', 그 미만은 '억'.
function formatWon(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 10000) {
    return `${(v / 10000).toFixed(abs >= 100000 ? 0 : 1)}조`;
  }
  return `${Math.round(v).toLocaleString("ko-KR")}억`;
}
const UNIT_FORMAT: Record<Unit, { formatter: (v: number) => string; minMove: number }> = {
  won: { formatter: formatWon, minMove: 1 },
  krw: { formatter: (v) => `${Math.round(v).toLocaleString("ko-KR")}원`, minMove: 1 },
  pct: { formatter: (v) => `${v.toFixed(1)}%`, minMove: 0.01 },
  mult: { formatter: (v) => `${v.toFixed(1)}배`, minMove: 0.01 },
};

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
  // 차트 재사용 + 억제창 + rAF emit 스로틀 + 자기 메아리 skip. CandleChart 와 동일 패턴.
  const chartRef = useRef<IChartApi | null>(null);
  const suppressUntilRef = useRef(0);
  const SUPPRESS_MS = 250;
  const emitRafRef = useRef(0);
  const pendingRef = useRef<{ from: string; to: string } | null>(null);
  const lastEmittedRef = useRef<{ from: string; to: string } | null>(null);
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
    // 그려질 지표들의 단위 집합 → 등장 순서대로 축 배정. 같은 단위는 한 축을 공유(비교 가능),
    // 앞의 두 단위는 오른쪽·왼쪽 실제 축(눈금·단위 레이블 표시), 나머지는 숨김 오버레이(형태만).
    const renderMetrics = METRICS.filter(
      (m) => active.has(m.key) && seriesData[m.key].length > 0,
    );
    const units: Unit[] = [];
    for (const m of renderMetrics) {
      if (!units.includes(m.unit)) {
        units.push(m.unit);
      }
    }
    // 단위 → priceScaleId. 첫째="right", 둘째="left", 그 외=단위명(숨김 오버레이).
    const scaleOf = (u: Unit): string => (u === units[0] ? "right" : u === units[1] ? "left" : u);

    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: COLOR_TEXT, fontFamily: "inherit" },
      grid: { vertLines: { color: COLOR_GRID }, horzLines: { color: COLOR_GRID } },
      rightPriceScale: { borderColor: COLOR_GRID, visible: units.length > 0 },
      leftPriceScale: { borderColor: COLOR_GRID, visible: units.length > 1 },
      timeScale: { borderColor: COLOR_GRID },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR" },
    });

    let any = false;
    for (const m of renderMetrics) {
      const scaleId = scaleOf(m.unit);
      const fmt = UNIT_FORMAT[m.unit];
      const line = chart.addSeries(LineSeries, {
        color: m.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: scaleId,
        priceFormat: { type: "custom", formatter: fmt.formatter, minMove: fmt.minMove },
      });
      // 숨김 오버레이(3번째 이후 단위)만 축을 감춘다. right/left 는 눈금이 보이게 둔다.
      if (scaleId !== "right" && scaleId !== "left") {
        chart.priceScale(scaleId).applyOptions({ visible: false });
      }
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
    // range 의도적 제외(아래 별도 effect 가 재생성 없이 반영). CandleChart 와 동일.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesData, active]);

  // range 변경만 반영(차트 재생성 없이). 자기가 방금 내보낸 구간이면 이미 그 위치라 skip.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !range) {
      return;
    }
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
