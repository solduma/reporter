"use client";

import { useEffect, useState } from "react";

import DeepDiveReportView from "@/components/DeepDiveReportView";
import { fetchSharedDeepDive } from "@/lib/api";
import type { SharedDeepDive } from "@/lib/types";

import styles from "./page.module.css";

// 만료까지 남은 분(음수면 만료). 표시용.
function minutesLeft(expiresAt: string): number {
  return Math.floor((new Date(expiresAt).getTime() - Date.now()) / 60000);
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

      <DeepDiveReportView report={data.report} />

      <footer className={styles.footer}>
        Report Pulse · 이 페이지는 공유 시점의 스냅샷이며 30분 후 만료됩니다.
      </footer>
    </div>
  );
}
