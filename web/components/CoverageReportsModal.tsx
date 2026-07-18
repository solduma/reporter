"use client";

import { useEffect, useState } from "react";

import { fetchCoverageReports } from "@/lib/api";
import type { Report } from "@/lib/types";

import ReportColumn from "./ReportColumn";
import styles from "./CoverageReportsModal.module.css";

interface Props {
  code: string;
  onClose: () => void;
}

type State = { status: "loading" | "ready" | "error"; data: Report[]; message?: string };

export default function CoverageReportsModal({ code, onClose }: Props) {
  const [state, setState] = useState<State>({ status: "loading", data: [] });

  useEffect(() => {
    let active = true;
    fetchCoverageReports(code)
      .then((data) => {
        if (active) setState({ status: "ready", data });
      })
      .catch((e: unknown) => {
        if (active)
          setState({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "리포트를 불러오지 못했습니다",
          });
      });
    return () => {
      active = false;
    };
  }, [code]);

  // Escape 닫기 + 배경 스크롤 락.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  const loading = state.status === "loading";
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
        <div className={styles.header}>
          <div>
            <h2 className={styles.title}>리포트 커버리지</h2>
            <p className={styles.sub}>
              최근 1년 · {loading ? "…" : `${state.data.length}건`}
            </p>
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>

        {state.status === "error" ? (
          <p className={styles.error}>불러오기 실패: {state.message}</p>
        ) : (
          <div className={styles.columns}>
            <ReportColumn
              reports={company}
              loading={loading}
              title="종목 리포트"
              icon="📈"
              emptyLabel="종목 리포트가 없습니다"
            />
            <ReportColumn
              reports={industry}
              loading={loading}
              title="산업 리포트"
              icon="🏭"
              emptyLabel="산업 리포트가 없습니다"
            />
          </div>
        )}
      </div>
    </div>
  );
}
