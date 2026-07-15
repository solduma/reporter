"use client";

import { useEffect, useState } from "react";

import { fetchCompanyGrowth } from "@/lib/api";
import type { CompanyGrowth } from "@/lib/types";

import styles from "./GrowthMetrics.module.css";

// 성장 지표는 다른 섹션과 독립적으로 로딩/실패하도록 상태를 분리한다.
type State = { status: "loading" | "ready" | "error"; data: CompanyGrowth | null; message?: string };

// YoY 비율(0.2527)을 소수 첫째자리 퍼센트("+25.3%")로 표기.
function formatYoy(ratio: number | null): string {
  if (ratio === null) {
    return "—";
  }
  const pct = ratio * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

// 이익률 변화 비율(0.559)을 pp("+55.9pp")로 표기.
function formatPp(ratio: number | null): string | null {
  if (ratio === null) {
    return null;
  }
  const pp = ratio * 100;
  const sign = pp > 0 ? "+" : "";
  return `${sign}${pp.toFixed(1)}pp`;
}

// 성장 지표 색: 개선 초록 / 악화 빨강
function growthClass(value: number | null): string {
  if (value === null || value === 0) {
    return styles.flat;
  }
  return value > 0 ? styles.gpos : styles.gneg;
}

// 손익 4상태 배지: 흑자전환은 강조, 흑자지속은 초록, 적자전환/적자지속은 빨강.
function statusClass(status: string): string {
  if (status === "흑자전환") {
    return styles.turnaround;
  }
  if (status === "흑자지속") {
    return `${styles.statusBadge} ${styles.statusPos}`;
  }
  return `${styles.statusBadge} ${styles.statusNeg}`;
}

// 이익 지표 타일(영업이익·순이익·EBITDA 공용) — 손익상태 배지 + 이익률 증감 pp.
// 성장 점수의 영업이익/순이익/EBITDA 축과 동일한 상태+마진 표현이라 근거와 화면이 일치한다.
function ProfitTile({
  label,
  status,
  marginDelta,
}: {
  label: string;
  status: string | null;
  marginDelta: number | null;
}) {
  const pp = formatPp(marginDelta);
  return (
    <div className={styles.tile}>
      <span className={styles.label}>{label}</span>
      <span className={pp === null ? `${styles.value} ${styles.muted}` : `${styles.value} ${growthClass(marginDelta)}`}>
        {pp ?? "—"}
      </span>
      {status ? <span className={statusClass(status)}>{status}</span> : null}
      <span className={styles.sub}>이익률 증감</span>
    </div>
  );
}

export default function GrowthMetrics({ code }: { code: string }) {
  const [state, setState] = useState<State>({ status: "loading", data: null });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", data: null });
      try {
        const res = await fetchCompanyGrowth(code);
        if (active) {
          setState({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: null,
            message: e instanceof Error ? e.message : "성장 지표를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  if (state.status === "loading") {
    return <div className={styles.status}>불러오는 중…</div>;
  }
  if (state.status === "error") {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }

  const g = state.data;
  if (g === null) {
    return <div className={styles.status}>성장 지표가 없습니다</div>;
  }

  const periodLabel = g.period ? `기준 ${g.period}` : null;

  return (
    <div className={styles.grid}>
      <div className={styles.tile}>
        <span className={styles.label}>매출 YoY</span>
        <span
          className={
            g.revenue_yoy === null
              ? `${styles.value} ${styles.muted}`
              : `${styles.value} ${growthClass(g.revenue_yoy)}`
          }
        >
          {formatYoy(g.revenue_yoy)}
        </span>
        {periodLabel ? <span className={styles.sub}>{periodLabel}</span> : null}
      </div>

      <ProfitTile label="영업이익" status={g.op_status} marginDelta={g.op_margin_delta} />
      <ProfitTile label="순이익" status={g.net_status} marginDelta={g.net_margin_delta} />
      <ProfitTile label="EBITDA" status={g.ebitda_status} marginDelta={g.ebitda_margin_delta} />
    </div>
  );
}
