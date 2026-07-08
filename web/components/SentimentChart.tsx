"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { SentimentPoint } from "@/lib/types";

import styles from "./SentimentChart.module.css";

// SVG stroke/fill 속성은 CSS 변수를 해석하지 못하므로 globals.css 토큰과 동일한 값을 직접 둔다.
const COLOR_BUY = "#128a4d";
const COLOR_SELL = "#c02b2b";
const COLOR_LINE = "#7b4b2a";
const COLOR_AXIS = "#6b7280";
const COLOR_GRID = "#e4e7eb";

interface Props {
  data: SentimentPoint[];
  selectedDate: string | null;
  onSelectPoint: (point: SentimentPoint) => void;
}

interface DotProps {
  cx?: number;
  cy?: number;
  index?: number;
  payload?: SentimentPoint;
}

interface TooltipProps {
  active?: boolean;
  payload?: { payload: SentimentPoint }[];
}

// LineChart 클릭 시 커서에 가장 가까운 데이터 포인트를 넘겨받기 위한 최소 형태.
interface ChartClickState {
  activePayload?: { payload: SentimentPoint }[];
}

function formatDay(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
}

function sentimentColor(value: number): string {
  if (value > 0) {
    return COLOR_BUY;
  }
  if (value < 0) {
    return COLOR_SELL;
  }
  return COLOR_AXIS;
}

function ChartTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  const point = payload[0].payload;
  return (
    <div className={styles.tooltip}>
      <span className={styles.tooltipDate}>{formatDay(point.date)}</span>
      <span className={styles.tooltipValue} style={{ color: sentimentColor(point.avg_sentiment) }}>
        평균 {point.avg_sentiment.toFixed(2)}
      </span>
      <span className={styles.tooltipCount}>리포트 {point.reports.length}건</span>
    </div>
  );
}

export default function SentimentChart({ data, selectedDate, onSelectPoint }: Props) {
  const renderDot = (props: DotProps) => {
    const { cx, cy, index, payload } = props;
    if (cx === undefined || cy === undefined || !payload) {
      return <g key={`empty-${index ?? 0}`} />;
    }
    const selected = payload.date === selectedDate;
    const color = sentimentColor(payload.avg_sentiment);
    return (
      <g
        key={payload.date}
        className={styles.dotGroup}
        onClick={() => onSelectPoint(payload)}
        role="button"
        tabIndex={0}
        aria-label={`${formatDay(payload.date)} 리포트 보기`}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            onSelectPoint(payload);
          }
        }}
      >
        {/* 실제 점보다 넓은 투명 히트 영역 */}
        <circle cx={cx} cy={cy} r={13} fill="transparent" />
        <circle
          cx={cx}
          cy={cy}
          r={selected ? 7 : 4.5}
          fill={color}
          stroke="#ffffff"
          strokeWidth={2}
        />
      </g>
    );
  };

  const handleChartClick = (state: ChartClickState) => {
    const nearest = state.activePayload?.[0]?.payload;
    if (nearest) {
      onSelectPoint(nearest);
    }
  };

  return (
    <ResponsiveContainer width="100%" height={340}>
      <LineChart
        data={data}
        margin={{ top: 16, right: 24, bottom: 8, left: 0 }}
        onClick={handleChartClick}
      >
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={formatDay}
          tick={{ fill: COLOR_AXIS, fontSize: 12 }}
          stroke={COLOR_GRID}
          tickMargin={8}
        />
        <YAxis
          domain={[-1, 1]}
          ticks={[-1, -0.5, 0, 0.5, 1]}
          tick={{ fill: COLOR_AXIS, fontSize: 12 }}
          stroke={COLOR_GRID}
          width={40}
        />
        <ReferenceLine y={0} stroke={COLOR_AXIS} strokeWidth={1.5} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLOR_GRID, strokeWidth: 1 }} />
        <Line
          type="monotone"
          dataKey="avg_sentiment"
          stroke={COLOR_LINE}
          strokeWidth={2}
          dot={renderDot}
          activeDot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
