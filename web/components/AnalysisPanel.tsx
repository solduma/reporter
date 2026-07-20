"use client";

import { useEffect, useState } from "react";

import InfoDot from "@/components/InfoDot";
import { fetchCompanyAnalysis } from "@/lib/api";
import type { AnalysisAxis, CompanyAnalysis } from "@/lib/types";

import styles from "./AnalysisPanel.module.css";

// 축별 초보자 설명(무엇을 보는 축인지 + 점수 해석). 서버 axis.key 기준.
const AXIS_INFO: Record<string, { what: string; guide: string }> = {
  growth: {
    what: "매출·영업이익이 얼마나 빠르게 크는지(성장주 관점).",
    guide: "60↑ 고성장 축, 40↓ 정체. 같은 후보군 내 상대 점수.",
  },
  value: {
    what: "저평가 정도(저PER·저PBR·저EV/EBITDA + 고ROE·고배당).",
    guide: "60↑ 저평가 우위, 40↓ 고평가. 자산·수익가치 기준.",
  },
  technical: {
    what: "주가 추세·모멘텀(신고가 근접·이평 정배열·거래량).",
    guide: "60↑ 상승 추세 강함(주도주 성격), 40↓ 약함.",
  },
  topdown: {
    what: "이 종목이 속한 섹터로 돈이 도는지(미국 섹터가 국내 선행).",
    guide: "60↑ 섹터 자금유입 우호, 40↓ 자금 이탈.",
  },
};

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
  const info = AXIS_INFO[axis.key];
  // 계산 방식 설명: 서버 method 우선, 없으면 초보자 축 설명으로 폴백.
  const methodText = axis.method ?? info?.what;
  // 기본은 헤더(라벨·점수)만, 클릭 시 상세 지표(metrics·근거) 노출.
  return (
    <details className={styles.axis}>
      <summary className={styles.axisHead}>
        <span className={styles.axisLabel}>
          {axis.label}
          {info ? <InfoDot what={info.what} guide={info.guide} /> : null}
        </span>
        <span className={`${styles.axisScore} ${scoreClass(axis.score)}`}>
          {scoreText(axis.score)}
        </span>
      </summary>
      <dl className={styles.metrics}>
        {axis.metrics.map((m) => (
          <div key={m.label} className={styles.metric}>
            <dt className={styles.metricLabel}>{m.label}</dt>
            <dd className={styles.metricValue}>{m.value}</dd>
          </div>
        ))}
      </dl>
      {axis.factors && axis.factors.length > 0 ? (
        <div className={styles.factorBlock}>
          <span className={styles.factorHead}>
            점수 근거
            {methodText ? <InfoDot what={`계산 방식: ${methodText}`} /> : null}
          </span>
          <ul className={styles.factorList}>
            {axis.factors.map((f) => {
              const contrib = f.norm === null ? null : Math.round(f.norm * f.weight * 100);
              const pct = f.norm === null ? 0 : Math.round(f.norm * 100);
              return (
                <li key={f.label} className={styles.factorRow}>
                  <span className={styles.factorLabel} title={f.label}>
                    {f.label}
                  </span>
                  <span className={styles.factorVal}>{f.value}</span>
                  <span className={styles.factorBar} aria-hidden>
                    <span className={styles.factorBarFill} style={{ width: `${pct}%` }} />
                  </span>
                  <span
                    className={styles.factorContrib}
                    title={`가중치 ${Math.round(f.weight * 100)}% · 기여 ${
                      contrib === null ? "제외" : `${contrib}점`
                    }`}
                  >
                    {contrib === null ? "—" : `+${contrib}`}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </details>
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

  const j = a.judgment;
  return (
    <div className={styles.panel}>
      {j && j.signal !== "insufficient" ? (
        <div className={styles.judgment}>
          <div className={styles.judgHead}>
            <span className={styles.judgTitle}>한눈에</span>
            <span className={`${styles.signal} ${styles[`sig_${j.signal}`]}`}>
              {j.signal_label}
            </span>
          </div>
          <div className={styles.judgCols}>
            {j.strengths.length > 0 ? (
              <div className={styles.judgCol}>
                <span className={styles.judgColLabel}>강점</span>
                <ul className={styles.judgList}>
                  {j.strengths.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {j.weaknesses.length > 0 ? (
              <div className={styles.judgCol}>
                <span className={styles.judgColLabel}>약점</span>
                <ul className={styles.judgList}>
                  {j.weaknesses.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {j.checks.length > 0 ? (
              <div className={styles.judgCol}>
                <span className={styles.judgColLabel}>확인할 점</span>
                <ul className={styles.judgList}>
                  {j.checks.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
          <p className={styles.disclaimer}>
            점수 기반 정보 요약입니다. 투자 판단과 책임은 본인에게 있습니다.
          </p>
        </div>
      ) : null}

      <div className={styles.overallRow}>
        <span className={styles.overallLabel}>종합 점수</span>
        <span className={`${styles.overallScore} ${scoreClass(a.overall_score)}`}>
          {scoreText(a.overall_score)}
          <span className={styles.overallMax}>/100</span>
        </span>
        <span className={styles.baseline}>60↑ 양호 · 40↓ 약함 (후보군 내 상대 점수)</span>
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
