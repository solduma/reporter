"use client";

import { useRealtimeQuote } from "@/lib/useRealtimeQuote";

import styles from "./RealtimeQuoteBadge.module.css";

// 종목 헤더 옆 실시간 현재가·등락 배지. 국내 6자리 코드만. 틱이 없으면(장 마감·비활성) 아무것도
// 렌더하지 않아 정적 페이지에 방해되지 않는다.
export default function RealtimeQuoteBadge({ code }: { code: string }) {
  const quote = useRealtimeQuote(code);
  if (!quote) {
    return null;
  }
  const tone = quote.rising === true ? styles.up : quote.rising === false ? styles.down : styles.flat;
  const arrow = quote.rising === true ? "▲" : quote.rising === false ? "▼" : "–";
  const sign = quote.change_ratio > 0 ? "+" : quote.change_ratio < 0 ? "−" : "";
  const ratio = Math.abs(quote.change_ratio).toFixed(2);
  return (
    <span className={`${styles.badge} ${tone}`} title={`실시간 · ${formatTime(quote.ts)}`}>
      <span className={styles.live} aria-hidden />
      <span className={styles.price}>{quote.price.toLocaleString("ko-KR")}</span>
      <span className={styles.change}>
        <span aria-hidden>{arrow}</span>
        {sign}
        {ratio}%
      </span>
    </span>
  );
}

// HHMMSS → HH:MM:SS
function formatTime(ts: string): string {
  if (ts.length !== 6) {
    return ts;
  }
  return `${ts.slice(0, 2)}:${ts.slice(2, 4)}:${ts.slice(4, 6)}`;
}
