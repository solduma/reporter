"use client";

import { useEffect, useState } from "react";

import DeepDiveReportView, { isMultiMethodValuation } from "@/components/DeepDiveReportView";
import { fetchSharedDeepDive } from "@/lib/api";
import type { DeepDiveReport, SharedDeepDive } from "@/lib/types";

import styles from "./page.module.css";

// 만료까지 남은 분(음수면 만료). 표시용.
function minutesLeft(expiresAt: string): number {
  return Math.floor((new Date(expiresAt).getTime() - Date.now()) / 60000);
}

function fmtWon(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${Math.round(n).toLocaleString("ko-KR")}원`;
}

// 최종 목표가·업사이드·현재가를 최상단에 강조. 다중방식 밸류에이션(final_target_price 보유)일 때만.
function TargetBanner({ report }: { report: DeepDiveReport }) {
  const v = report.valuation;
  if (!isMultiMethodValuation(v)) {
    return null;
  }
  const val = v as { final_target_price: number | null; final_upside_pct: number | null; current_price: number | null };
  if (val.final_target_price === null) {
    return null;
  }
  const up = val.final_upside_pct;
  const upClass = up === null ? "" : up >= 0 ? styles.upPos : styles.upNeg;
  return (
    <div className={styles.banner}>
      <span className={styles.bannerLabel}>최종 목표가</span>
      <span className={styles.bannerPrice}>{fmtWon(val.final_target_price)}</span>
      {up !== null ? (
        <span className={`${styles.bannerUpside} ${upClass}`}>
          {up >= 0 ? "+" : ""}
          {up.toFixed(1)}%
        </span>
      ) : null}
      {val.current_price !== null ? (
        <span className={styles.bannerCurrent}>현재가 {fmtWon(val.current_price)}</span>
      ) : null}
    </div>
  );
}

export default function SharePage({ params }: { params: { token: string } }) {
  const [data, setData] = useState<SharedDeepDive | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchSharedDeepDive(params.token)
      .then((d) => {
        if (active) setData(d);
      })
      .catch(() => {
        if (active) setError("만료되었거나 존재하지 않는 공유 링크입니다.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [params.token]);

  if (loading) {
    return <div className={styles.state}>불러오는 중…</div>;
  }
  if (error || !data) {
    return (
      <div className={styles.state}>
        <p className={styles.errorTitle}>공유 링크를 열 수 없습니다</p>
        <p className={styles.errorBody}>{error ?? "존재하지 않는 링크입니다."}</p>
        <p className={styles.errorBody}>공유 링크는 생성 후 30분간만 유효합니다.</p>
      </div>
    );
  }

  const left = minutesLeft(data.expires_at);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>
            {data.stock_name ?? data.stock_code}
            <span className={styles.code}>{data.stock_code}</span>
          </h1>
          <button type="button" className={styles.pdfBtn} onClick={() => window.print()}>
            PDF 다운로드
          </button>
        </div>
        <p className={styles.meta}>
          딥다이브 공유 · {data.report.as_of ? `생성 ${data.report.as_of.slice(0, 10)}` : ""} ·{" "}
          {left > 0 ? `${left}분 후 만료` : "곧 만료"}
        </p>
      </header>

      <TargetBanner report={data.report} />

      <DeepDiveReportView report={data.report} />

      <footer className={styles.footer}>
        Report Pulse · 이 페이지는 공유 시점의 스냅샷이며 30분 후 만료됩니다.
      </footer>
    </div>
  );
}
