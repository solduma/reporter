"use client";

import { useEffect, useState } from "react";

import { fetchCoverageReports } from "@/lib/api";
import type { Report } from "@/lib/types";

import styles from "./CoverageReportsModal.module.css";
import ReportColumn from "./ReportColumn";

type State = { status: "loading" | "ready" | "error"; data: Report[]; message?: string };

// 커버리지 타일 클릭 시 노출 — 종목 리포트 + 종목이 속한 산업 리포트를 한 목록으로.
export default function CoverageReportsModal({
  code,
  onClose,
}: {
  code: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<State>({ status: "loading", data: [] });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const res = await fetchCoverageReports(code);
        if (active) {
          setState({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "리포트를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  const company = state.data.filter((r) => r.category === "company");
  const industry = state.data.filter((r) => r.category === "industry");

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div
        className={styles.modal}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="리포트 커버리지"
      >
        <header className={styles.header}>
          <span className={styles.headerTitle}>
            리포트 커버리지
            {state.status === "ready" ? (
              <span className={styles.headerCount}>최근 1년 · {state.data.length}건</span>
            ) : null}
          </span>
          <button type="button" className={styles.closeButton} onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </header>
        <div className={styles.body}>
          {state.status === "error" ? (
            <p className={styles.error}>API 연결 실패: {state.message}</p>
          ) : (
            <div className={styles.columns}>
              <ReportColumn
                reports={company}
                loading={state.status === "loading"}
                title="종목 리포트"
                icon="📈"
                emptyLabel="종목 리포트가 없습니다"
              />
              <ReportColumn
                reports={industry}
                loading={state.status === "loading"}
                title="산업 리포트"
                icon="🏭"
                emptyLabel="산업 리포트가 없습니다"
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
