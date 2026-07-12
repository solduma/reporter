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

// lightweight-charts Time → 'YYYY-MM-DD'. 차트 조작(스크롤·드래그)으로 바뀐 표시 구간을
// 페이지 date-range(ISO 문자열)로 되돌리는 역변환. 문자열 Time 은 앞 10자, timestamp 는 UTC 일자,
// BusinessDay 객체는 y-m-d 조립.
export function tsToDate(time: Time): string {
  if (typeof time === "string") {
    return time.slice(0, 10);
  }
  if (typeof time === "number") {
    return new Date(time * 1000).toISOString().slice(0, 10);
  }
  const { year, month, day } = time;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

// n개월 전 'YYYY-MM-DD'. date-range 기본값(최근 3개월) 계산용.
export function monthsAgoIso(months: number, from: Date): string {
  const d = new Date(from);
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}
