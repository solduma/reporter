"use client";

import { useEffect, useState } from "react";

import { fetchCompanyGrowth } from "@/lib/api";
import type { CompanyGrowth } from "@/lib/types";

import CoverageReportsModal from "./CoverageReportsModal";
import styles from "./GrowthMetrics.module.css";

// 종목 일반 정보(시총·현재가·모멘텀·커버리지) 스냅샷 — 성장 지표와 분리해 페이지 최상단에 둔다.
// 성장 축 점수와 무관한 기본 정보라, 성장 지표(GrowthMetrics)와 같은 CompanyGrowth 응답을 쓰되
// 각 섹션이 독립 로딩하는 기존 패턴을 따른다.
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

// 등락률 색: 한국 관행 — 상승 빨강 / 하락 파랑
function changeClass(pct: number | null): string {
  if (pct === null || pct === 0) {
    return styles.flat;
  }
  return pct > 0 ? styles.up : styles.down;
}

// 모멘텀 색: 개선 초록 / 악화 빨강 (수익률 관례)
function momentumClass(value: number | null): string {
  if (value === null || value === 0) {
    return styles.flat;
  }
  return value > 0 ? styles.gpos : styles.gneg;
}

export default function CompanySnapshot({ code }: { code: string }) {
  const [state, setState] = useState<State>({ status: "loading", data: null });
  const [coverageOpen, setCoverageOpen] = useState(false);

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
            message: e instanceof Error ? e.message : "종목 정보를 불러오지 못했습니다",
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
    return <div className={styles.status}>종목 정보가 없습니다</div>;
  }

  const buyLabel =
    g.buy_ratio === null ? null : `BUY ${Math.round(g.buy_ratio * 100).toLocaleString("ko-KR")}%`;

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
        <span className={`${styles.sub} ${changeClass(g.change_pct)}`}>{formatPct(g.change_pct)}</span>
      </div>

      <div className={styles.tile}>
        <span className={styles.label}>3개월 모멘텀</span>
        <span
          className={
            g.momentum_3m === null
              ? `${styles.value} ${styles.muted}`
              : `${styles.value} ${momentumClass(g.momentum_3m)}`
          }
        >
          {formatPct(g.momentum_3m)}
        </span>
        <span className={styles.sub}>3개월 수익률</span>
      </div>

      {g.coverage_count > 0 ? (
        <button
          type="button"
          className={`${styles.tile} ${styles.tileButton}`}
          onClick={() => setCoverageOpen(true)}
        >
          <span className={styles.label}>
            리포트 커버리지 <span className={styles.chevron}>›</span>
          </span>
          <span className={styles.value}>
            {g.coverage_count.toLocaleString("ko-KR")}건
          </span>
          <span className={buyLabel ? `${styles.sub} ${styles.buyTag}` : styles.sub}>
            {buyLabel ?? "최근 1년 · 종목·산업"}
          </span>
        </button>
      ) : (
        <div className={styles.tile}>
          <span className={styles.label}>리포트 커버리지</span>
          <span className={`${styles.value} ${styles.muted}`}>—</span>
          <span className={styles.sub}>최근 1년</span>
        </div>
      )}

      {coverageOpen ? (
        <CoverageReportsModal code={code} onClose={() => setCoverageOpen(false)} />
      ) : null}
    </div>
  );
}
