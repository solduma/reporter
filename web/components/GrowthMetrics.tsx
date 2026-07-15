"use client";

import { useEffect, useState } from "react";

import { fetchCompanyGrowth } from "@/lib/api";
import type { CompanyGrowth } from "@/lib/types";

import styles from "./GrowthMetrics.module.css";

// 성장 지표는 다른 섹션과 독립적으로 로딩/실패하도록 상태를 분리한다.
type State = { status: "loading" | "ready" | "error"; data: CompanyGrowth | null; message?: string };

const EOK = 100_000_000; // 1억 = 1e8원

function formatEok(won: number | null): string {
  if (won === null) {
    return "—";
  }
  return `${Math.round(won / EOK).toLocaleString("ko-KR")}억`;
}

function formatPrice(price: number | null): string {
  if (price === null) {
    return "—";
  }
  return `${price.toLocaleString("ko-KR")}원`;
}

function formatPct(pct: number | null): string {
  if (pct === null) {
    return "—";
  }
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

// YoY 비율(0.2527)을 소수 첫째자리 퍼센트("+25.3%")로 표기.
function formatYoy(ratio: number | null): string {
  if (ratio === null) {
    return "—";
  }
  const pct = ratio * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

// 영업이익률 변화 비율(0.559)을 pp("+55.9pp")로 표기 — 흑자전환 규모.
function formatPp(ratio: number | null): string | null {
  if (ratio === null) {
    return null;
  }
  const pp = ratio * 100;
  const sign = pp > 0 ? "+" : "";
  return `${sign}${pp.toFixed(1)}pp`;
}

// 등락률 색: 한국 관행 — 상승 빨강 / 하락 파랑
function changeClass(pct: number | null): string {
  if (pct === null || pct === 0) {
    return styles.flat;
  }
  return pct > 0 ? styles.up : styles.down;
}

// 성장 지표(매출·영업이익·모멘텀) 색: 개선 초록 / 악화 빨강 (등락률과 별개 관례)
function growthClass(value: number | null): string {
  if (value === null || value === 0) {
    return styles.flat;
  }
  return value > 0 ? styles.gpos : styles.gneg;
}

// 영업손익 4상태 배지: 흑자전환은 강조(turnaround), 흑자지속은 초록, 적자전환/적자지속은 빨강.
function opStatusClass(status: string): string {
  if (status === "흑자전환") {
    return styles.turnaround;
  }
  if (status === "흑자지속") {
    return `${styles.statusBadge} ${styles.statusPos}`;
  }
  return `${styles.statusBadge} ${styles.statusNeg}`;
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

  const buyLabel =
    g.buy_ratio === null ? null : `BUY ${Math.round(g.buy_ratio * 100).toLocaleString("ko-KR")}%`;
  const periodLabel = g.period ? `기준 ${g.period}` : null;

  return (
    <div className={styles.grid}>
      <div className={styles.tile}>
        <span className={styles.label}>시가총액</span>
        <span className={g.market_cap === null ? `${styles.value} ${styles.muted}` : styles.value}>
          {formatEok(g.market_cap)}
        </span>
        <span className={styles.sub}>{g.market ?? "—"}</span>
      </div>

      <div className={styles.tile}>
        <span className={styles.label}>현재가</span>
        <span className={g.close_price === null ? `${styles.value} ${styles.muted}` : styles.value}>
          {formatPrice(g.close_price)}
        </span>
        <span className={`${styles.sub} ${changeClass(g.change_pct)}`}>
          {formatPct(g.change_pct)}
        </span>
      </div>

      <div className={styles.tile}>
        <span className={styles.label}>3개월 모멘텀</span>
        <span
          className={
            g.momentum_3m === null
              ? `${styles.value} ${styles.muted}`
              : `${styles.value} ${growthClass(g.momentum_3m)}`
          }
        >
          {formatPct(g.momentum_3m)}
        </span>
        <span className={styles.sub}>3개월 수익률</span>
      </div>

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

      <div className={styles.tile}>
        <span className={styles.label}>영업이익 YoY</span>
        {/* 흑자전환은 직전 적자라 YoY 비율이 없다(왜곡) → 대신 흑자전환 규모(Δ영업이익률 pp)를 보여준다. */}
        {g.op_turnaround && g.op_margin_delta !== null ? (
          <span className={`${styles.value} ${styles.gpos}`}>{formatPp(g.op_margin_delta)}</span>
        ) : (
          <span
            className={
              g.op_yoy === null
                ? `${styles.value} ${styles.muted}`
                : `${styles.value} ${growthClass(g.op_yoy)}`
            }
          >
            {formatYoy(g.op_yoy)}
          </span>
        )}
        {g.op_status ? (
          <span className={opStatusClass(g.op_status)}>{g.op_status}</span>
        ) : null}
        {g.op_turnaround && g.op_margin_delta !== null ? (
          <span className={styles.sub}>이익률 개선폭</span>
        ) : periodLabel ? (
          <span className={styles.sub}>{periodLabel}</span>
        ) : null}
      </div>

      <div className={styles.tile}>
        <span className={styles.label}>EPS YoY</span>
        <span
          className={
            g.eps_yoy === null
              ? `${styles.value} ${styles.muted}`
              : `${styles.value} ${growthClass(g.eps_yoy)}`
          }
        >
          {formatYoy(g.eps_yoy)}
        </span>
        <span className={styles.sub}>주당순이익 성장</span>
      </div>

      <div className={styles.tile}>
        <span className={styles.label}>리포트 커버리지</span>
        <span
          className={
            g.coverage_count > 0 ? styles.value : `${styles.value} ${styles.muted}`
          }
        >
          {g.coverage_count > 0 ? `${g.coverage_count.toLocaleString("ko-KR")}건` : "—"}
        </span>
        {g.coverage_count > 0 && buyLabel ? (
          <span className={`${styles.sub} ${styles.buyTag}`}>{buyLabel}</span>
        ) : (
          <span className={styles.sub}>최근 1년</span>
        )}
      </div>
    </div>
  );
}
