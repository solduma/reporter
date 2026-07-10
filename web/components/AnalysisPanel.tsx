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

interface Props {
  code: string;
  // 부모가 이미 /analysis 를 조회했으면 주입해 중복 호출(중복 LLM 코멘트)을 막는다.
  // 미주입 시 자체 조회(다른 페이지에서 단독 사용 가능).
  analysis?: CompanyAnalysis | null;
  status?: "loading" | "ready" | "error";
  message?: string;
}

export default function AnalysisPanel({ code, analysis, status, message }: Props) {
  const [selfState, setSelfState] = useState<State>({ status: "loading", data: null });
  const controlled = status !== undefined;

  useEffect(() => {
    if (controlled) {
      return; // 부모가 제어 → 자체 조회 안 함
    }
    let active = true;
    async function load() {
      setSelfState({ status: "loading", data: null });
      try {
        const res = await fetchCompanyAnalysis(code);
        if (active) {
          setSelfState({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setSelfState({
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
  }, [code, controlled]);

  const state: State = controlled
    ? { status: status ?? "loading", data: analysis ?? null, message }
    : selfState;

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
      ) : a.comment_pending ? (
        <div className={styles.comment}>
          <span className={styles.commentTag}>AI 종합</span>
          <p className={styles.commentBody}>AI 종합 코멘트 생성 중…</p>
        </div>
      ) : (
        <p className={styles.note}>
          미국 섹터 선행은 안정 조회되는 반도체·기술·대형주 프록시 기준입니다.
        </p>
      )}
    </div>
  );
}
