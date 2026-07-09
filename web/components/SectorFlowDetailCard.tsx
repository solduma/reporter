"use client";

import { useEffect, useState } from "react";

import { fetchSectorFlowDetail } from "@/lib/api";
import type { SectorFlowDetail, SectorFlowRow } from "@/lib/types";

import styles from "./SectorFlowDetailCard.module.css";

type State = {
  status: "loading" | "ready" | "error";
  data: SectorFlowDetail | null;
  message?: string;
};

function scoreClass(score: number | null): string {
  if (score === null) {
    return styles.na;
  }
  if (score >= 60) {
    return styles.hot;
  }
  if (score >= 40) {
    return styles.warm;
  }
  return styles.cool;
}

function pct(v: number | null): string {
  if (v === null) {
    return "—";
  }
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function FlowBox({ label, row }: { label: string; row: SectorFlowRow | null }) {
  if (!row) {
    return (
      <div className={styles.box}>
        <span className={styles.boxLabel}>{label}</span>
        <span className={styles.na}>대응 섹터 없음</span>
      </div>
    );
  }
  return (
    <div className={styles.box}>
      <div className={styles.boxHead}>
        <span className={styles.boxLabel}>{label}</span>
        <span className={styles.sectorName}>{row.sector}</span>
      </div>
      <span className={`${styles.score} ${scoreClass(row.flow_score)}`}>
        {row.flow_score === null ? "—" : Math.round(row.flow_score)}
        <span className={styles.scoreMax}>/100</span>
      </span>
      <dl className={styles.metrics}>
        <div className={styles.metric}>
          <dt>3개월</dt>
          <dd>{pct(row.return_3m)}</dd>
        </div>
        <div className={styles.metric}>
          <dt>고점근접</dt>
          <dd>{row.near_high_pct === null ? "—" : `${row.near_high_pct.toFixed(0)}%`}</dd>
        </div>
        <div className={styles.metric}>
          <dt>거래량</dt>
          <dd>{row.vol_ratio === null ? "—" : `${row.vol_ratio.toFixed(2)}x`}</dd>
        </div>
        {row.foreign_delta !== null ? (
          <div className={styles.metric}>
            <dt>외국인Δ</dt>
            <dd>{`${row.foreign_delta > 0 ? "+" : ""}${row.foreign_delta.toFixed(2)}pp`}</dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

export default function SectorFlowDetailCard({ industry }: { industry: string }) {
  const [state, setState] = useState<State>({ status: "loading", data: null });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", data: null });
      try {
        const data = await fetchSectorFlowDetail(industry);
        if (active) {
          setState({ status: "ready", data });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: null,
            message: e instanceof Error ? e.message : "수급 flow를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [industry]);

  if (state.status === "loading") {
    return <div className={styles.status}>수급 계산 중…</div>;
  }
  if (state.status === "error") {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }

  const d = state.data;
  // 국내·미국 둘 다 매칭 안 되면 섹션 자체를 숨긴다(ETF 없는 산업).
  if (!d || (!d.kr && !d.us)) {
    return null;
  }

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h2 className={styles.title}>수급 flow (섹터 ETF)</h2>
        <p className={styles.subtitle}>
          주가 추세·거래량·신고가·외국인 수급 기반 자금유입 강도. 미국이 국내에 선행.
        </p>
      </div>
      <div className={styles.boxes}>
        <FlowBox label="국내" row={d.kr} />
        <FlowBox label="미국 (선행)" row={d.us} />
      </div>
    </section>
  );
}
