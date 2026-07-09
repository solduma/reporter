"use client";

import { useEffect, useState } from "react";

import { fetchCompanyAnalysis } from "@/lib/api";
import type { AnalysisAxis, CompanyAnalysis } from "@/lib/types";

import styles from "./AnalysisPanel.module.css";

type State = {
  status: "loading" | "ready" | "error";
  data: CompanyAnalysis | null;
  message?: string;
};

// 점수 0~100 → 색 등급. 60↑ 강세 / 40~60 중립 / 40↓ 약세.
function scoreClass(score: number | null): string {
  if (score === null) {
    return styles.scoreNa;
  }
  if (score >= 60) {
    return styles.scoreHigh;
  }
  if (score >= 40) {
    return styles.scoreMid;
  }
  return styles.scoreLow;
}

function scoreText(score: number | null): string {
  return score === null ? "—" : `${Math.round(score)}`;
}

function AxisCard({ axis }: { axis: AnalysisAxis }) {
  return (
    <div className={styles.axis}>
      <div className={styles.axisHead}>
        <span className={styles.axisLabel}>{axis.label}</span>
        <span className={`${styles.axisScore} ${scoreClass(axis.score)}`}>
          {scoreText(axis.score)}
        </span>
      </div>
      <dl className={styles.metrics}>
        {axis.metrics.map((m) => (
          <div key={m.label} className={styles.metric}>
            <dt className={styles.metricLabel}>{m.label}</dt>
            <dd className={styles.metricValue}>{m.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function AnalysisPanel({ code }: { code: string }) {
  const [state, setState] = useState<State>({ status: "loading", data: null });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", data: null });
      try {
        const res = await fetchCompanyAnalysis(code);
        if (active) {
          setState({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: null,
            message: e instanceof Error ? e.message : "분석을 불러오지 못했습니다",
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
    return <div className={styles.status}>분석 계산 중…</div>;
  }
  if (state.status === "error") {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }

  const a = state.data;
  if (a === null) {
    return <div className={styles.status}>분석 데이터가 없습니다</div>;
  }

  return (
    <div className={styles.panel}>
      <div className={styles.overallRow}>
        <span className={styles.overallLabel}>종합 점수</span>
        <span className={`${styles.overallScore} ${scoreClass(a.overall_score)}`}>
          {scoreText(a.overall_score)}
          <span className={styles.overallMax}>/100</span>
        </span>
      </div>

      <div className={styles.axes}>
        {a.axes.map((axis) => (
          <AxisCard key={axis.key} axis={axis} />
        ))}
      </div>

      {a.comment ? (
        <div className={styles.comment}>
          <span className={styles.commentTag}>AI 종합</span>
          <p className={styles.commentBody}>{a.comment}</p>
        </div>
      ) : (
        <p className={styles.note}>
          미국 섹터 선행(리버모어)은 안정 조회되는 반도체·기술·대형주 프록시 기준입니다.
        </p>
      )}
    </div>
  );
}
