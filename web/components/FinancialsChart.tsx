"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { FinancialPeriod } from "@/lib/types";

import styles from "./FinancialsChart.module.css";

// SVG는 CSS 변수를 해석하지 못하므로 토큰 값을 직접 둔다. 색은 dataviz 검증(CVD ΔE≥12)을 통과한 3계열.
const COLOR_REVENUE = "#2b6cc0";
const COLOR_OPERATING = "#128a4d";
const COLOR_NET = "#eb6834";
const COLOR_EPS = "#7b4b2a";
const COLOR_PER = "#2b6cc0";
const COLOR_PBR = "#eb6834";
const COLOR_AXIS = "#6b7280";
const COLOR_GRID = "#e4e7eb";

interface Props {
  data: FinancialPeriod[];
}

interface TooltipEntry {
  name: string;
  value: number | null;
  color: string;
}

interface TooltipProps {
  active?: boolean;
  label?: string;
  payload?: TooltipEntry[];
  unit?: string;
}

// 억원 단위 원값. 축은 조/억으로 압축, 툴팁은 천단위 콤마로 전체 표기한다.
function formatCompact(value: number): string {
  if (Math.abs(value) >= 10000) {
    return `${(value / 10000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조`;
  }
  return value.toLocaleString("ko-KR");
}

function formatFull(value: number, unit: string): string {
  return `${value.toLocaleString("ko-KR")}${unit}`;
}

function ChartTooltip({ active, label, payload, unit = "" }: TooltipProps) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  return (
    <div className={styles.tooltip}>
      <span className={styles.tooltipLabel}>{label}</span>
      {payload.map((entry) => (
        <span key={entry.name} className={styles.tooltipRow} style={{ color: entry.color }}>
          {entry.name} {entry.value === null ? "—" : formatFull(entry.value, unit)}
        </span>
      ))}
    </div>
  );
}

const AXIS_TICK = { fill: COLOR_AXIS, fontSize: 12 };

export default function FinancialsChart({ data }: Props) {
  // 추정 구간(E)은 막대를 흐리게 표시해 실적과 구분한다.
  const estimateOpacity = (isEstimate: boolean) => (isEstimate ? 0.4 : 1);

  return (
    <div className={styles.stack}>
      <figure className={styles.figure}>
        <figcaption className={styles.caption}>매출 · 영업이익 · 당기순이익 (억원)</figcaption>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="period" tick={AXIS_TICK} stroke={COLOR_GRID} tickMargin={8} />
            <YAxis
              tickFormatter={formatCompact}
              tick={AXIS_TICK}
              stroke={COLOR_GRID}
              width={56}
            />
            <Tooltip
              content={<ChartTooltip unit="억원" />}
              cursor={{ fill: "rgba(123, 75, 42, 0.06)" }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="revenue" name="매출" fill={COLOR_REVENUE} radius={[3, 3, 0, 0]}>
              {data.map((d) => (
                <Cell key={`rev-${d.period}`} fillOpacity={estimateOpacity(d.is_estimate)} />
              ))}
            </Bar>
            <Bar
              dataKey="operating_income"
              name="영업이익"
              fill={COLOR_OPERATING}
              radius={[3, 3, 0, 0]}
            >
              {data.map((d) => (
                <Cell key={`op-${d.period}`} fillOpacity={estimateOpacity(d.is_estimate)} />
              ))}
            </Bar>
            <Bar dataKey="net_income" name="당기순이익" fill={COLOR_NET} radius={[3, 3, 0, 0]}>
              {data.map((d) => (
                <Cell key={`net-${d.period}`} fillOpacity={estimateOpacity(d.is_estimate)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </figure>

      <div className={styles.row}>
        <figure className={styles.figure}>
          <figcaption className={styles.caption}>EPS (원)</figcaption>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
              <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tick={AXIS_TICK} stroke={COLOR_GRID} tickMargin={8} />
              <YAxis
                tickFormatter={formatCompact}
                tick={AXIS_TICK}
                stroke={COLOR_GRID}
                width={56}
              />
              <Tooltip content={<ChartTooltip unit="원" />} cursor={{ stroke: COLOR_GRID }} />
              <Line
                type="monotone"
                dataKey="eps"
                name="EPS"
                stroke={COLOR_EPS}
                strokeWidth={2}
                connectNulls
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </figure>

        <figure className={styles.figure}>
          <figcaption className={styles.caption}>PER · PBR (배)</figcaption>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
              <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tick={AXIS_TICK} stroke={COLOR_GRID} tickMargin={8} />
              <YAxis tick={AXIS_TICK} stroke={COLOR_GRID} width={44} />
              <Tooltip content={<ChartTooltip unit="배" />} cursor={{ stroke: COLOR_GRID }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="per"
                name="PER"
                stroke={COLOR_PER}
                strokeWidth={2}
                connectNulls
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="pbr"
                name="PBR"
                stroke={COLOR_PBR}
                strokeWidth={2}
                connectNulls
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </figure>
      </div>
    </div>
  );
}
