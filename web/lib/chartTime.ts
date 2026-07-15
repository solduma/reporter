import type { Time, UTCTimestamp } from "lightweight-charts";

import type { Timeframe } from "@/lib/types";

// CandlePoint.t → lightweight-charts Time. 30분봉은 벽시계 시각을 UTC 로 간주(표기 시각 유지),
// 일/주/월봉은 'YYYY-MM-DD' 날짜 문자열. CandleChart 와 동일 규칙(범위 계산에서 공유).
export function toChartTime(t: string, tf: Timeframe): Time {
  if (tf === "30m") {
    return (Date.parse(`${t}Z`) / 1000) as UTCTimestamp;
  }
  return t.slice(0, 10);
}

// 'YYYY-MM-DD' 날짜 문자열 → 자정 UTC timestamp(초). date-range 경계 계산용.
export function dateToTs(isoDate: string): UTCTimestamp {
  return (Date.parse(`${isoDate.slice(0, 10)}T00:00:00Z`) / 1000) as UTCTimestamp;
}

// n개월 전 'YYYY-MM-DD'. date-range 기본값(최근 3개월) 계산용.
export function monthsAgoIso(months: number, from: Date): string {
  const d = new Date(from);
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

// 기간 프리셋 단위(일/월/년) 만큼 이전 'YYYY-MM-DD'. 데이트레인지 퀵버튼(1주~10년)용.
export function agoIso(from: Date, unit: "day" | "month" | "year", amount: number): string {
  const d = new Date(from);
  if (unit === "day") {
    d.setDate(d.getDate() - amount);
  } else if (unit === "month") {
    d.setMonth(d.getMonth() - amount);
  } else {
    d.setFullYear(d.getFullYear() - amount);
  }
  return d.toISOString().slice(0, 10);
}
