"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { TradePoint } from "@/lib/types";

import styles from "./TradeChart.module.css";

// SVG는 CSS 변수를 해석하지 못하므로 globals.css 토큰 값을 직접 둔다.
const COLOR_EXPORT = "#128a4d";
const COLOR_IMPORT = "#c02b2b";
const COLOR_BALANCE = "#7b4b2a";
const COLOR_AXIS = "#6b7280";
const COLOR_GRID = "#e4e7eb";

interface Props {
  data: TradePoint[];
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
}

// USD 원값을 축·툴팁에서 압축 표기한다. 예: 16_400_000_000 → "$16.4B".
function formatUsdCompact(value: number): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1e9) {
    return `${sign}$${(abs / 1e9).toLocaleString("en-US", { maximumFractionDigits: 1 })}B`;
  }
  if (abs >= 1e6) {
    return `${sign}$${(abs / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 })}M`;
  }
  if (abs >= 1e3) {
    return `${sign}$${(abs / 1e3).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`;
  }
  return `${sign}$${abs.toLocaleString("en-US")}`;
}

const AXIS_TICK = { fill: COLOR_AXIS, fontSize: 12 };

function ChartTooltip({ active, label, payload }: TooltipProps) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  return (
    <div className={styles.tooltip}>
      <span className={styles.tooltipLabel}>{label}</span>
      {payload.map((entry) => (
        <span key={entry.name} className={styles.tooltipRow} style={{ color: entry.color }}>
          {entry.name} {entry.value === null ? "—" : formatUsdCompact(entry.value)}
        </span>
      ))}
    </div>
  );
}

export default function TradeChart({ data }: Props) {
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={data} margin={{ top: 16, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="period" tick={AXIS_TICK} stroke={COLOR_GRID} tickMargin={8} />
        <YAxis
          tickFormatter={formatUsdCompact}
          tick={AXIS_TICK}
          stroke={COLOR_GRID}
          width={64}
        />
        <ReferenceLine y={0} stroke={COLOR_AXIS} strokeWidth={1} />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(123, 75, 42, 0.06)" }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="export_usd" name="수출" fill={COLOR_EXPORT} radius={[3, 3, 0, 0]} />
        <Bar dataKey="import_usd" name="수입" fill={COLOR_IMPORT} radius={[3, 3, 0, 0]} />
        <Line
          type="monotone"
          dataKey="balance_usd"
          name="무역수지"
          stroke={COLOR_BALANCE}
          strokeWidth={2}
          dot={{ r: 3 }}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
