"use client";

import { useMemo } from "react";

import type { FinancialPeriod } from "@/lib/types";

import styles from "./CapexHistory.module.css";

interface Props {
  data: FinancialPeriod[];
}

// 연간 시설투자(CAPEX) 이력 — financials 응답의 capex(억원)를 막대로 시각화.
// 분기행은 대부분 null(연간 위주 산출)이므로 capex가 있는 기간만 오름차순 정렬해 표시.
export default function CapexHistory({ data }: Props) {
  const rows = useMemo(() => {
    return data
      .filter((d) => d.capex !== null && d.capex !== undefined)
      .map((d) => ({ period: d.period, capex: d.capex as number }))
      .sort((a, b) => a.period.localeCompare(b.period));
  }, [data]);

  if (rows.length === 0) {
    return <div className={styles.status}>시설투자 데이터가 없습니다</div>;
  }

  const max = Math.max(...rows.map((r) => Math.abs(r.capex)));
  // 0 기준 좌/우 막대(감소=음수 방어) — 단위 억원, 1만억(=1조) 이상 '조'.
  const fmt = (v: number) => {
    const abs = Math.abs(v);
    if (abs >= 10000) return `${(v / 10000).toFixed(abs >= 100000 ? 0 : 1)}조`;
    return `${Math.round(v).toLocaleString("ko-KR")}억`;
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.chart}>
        {rows.map((r) => {
          const pct = max > 0 ? (Math.abs(r.capex) / max) * 100 : 0;
          return (
            <div key={r.period} className={styles.row}>
              <span className={styles.period}>{r.period}</span>
              <div className={styles.barTrack}>
                <div
                  className={r.capex >= 0 ? styles.bar : styles.barNeg}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className={styles.value}>{fmt(r.capex)}</span>
            </div>
          );
        })}
      </div>
      <p className={styles.hint}>단위: 억원 · 자본적지출(유형+무형자산 취득)</p>
    </div>
  );
}