"use client";

import InfoDot from "@/components/InfoDot";
import type { AnalysisAxis } from "@/lib/types";

import styles from "./ScoreBreakdown.module.css";

// 점수 0~100 → 색 등급. 60↑ 강세 / 40~60 중립 / 40↓ 약세.
function scoreClass(score: number | null | undefined): string {
  if (score === null || score === undefined) {
    return styles.na;
  }
  if (score >= 60) {
    return styles.high;
  }
  if (score >= 40) {
    return styles.mid;
  }
  return styles.low;
}

function scoreText(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : `${Math.round(score)}`;
}

// 지표 카드 상단에 얹는 테크노펀더멘탈 축 점수 + 계산 근거(요소별 값·기여도) 요약.
// method(계산 방식)는 ⓘ hover 팝업으로, 각 요소는 기여도 막대로 '어떻게 이 점수가 나왔는지' 보인다.
export default function ScoreBreakdown({ axis }: { axis: AnalysisAxis | undefined }) {
  if (!axis) {
    return null;
  }
  const factors = axis.factors ?? [];
  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <span className={styles.label}>
          테크노펀더멘탈 {axis.label} 점수
          {axis.method ? <InfoDot what={`점수 계산 방식: ${axis.method}`} /> : null}
        </span>
        <span className={`${styles.score} ${scoreClass(axis.score)}`}>
          {scoreText(axis.score)}
          <span className={styles.max}>/100</span>
        </span>
      </div>
      {factors.length > 0 ? (
        <ul className={styles.factors}>
          {factors.map((f) => {
            const contrib = f.norm === null ? null : Math.round(f.norm * f.weight * 100);
            const pct = f.norm === null ? 0 : Math.round(f.norm * 100);
            return (
              <li key={f.label} className={styles.factor}>
                <span className={styles.fLabel}>{f.label}</span>
                <span className={styles.fValue}>{f.value}</span>
                <span className={styles.fBarTrack} aria-hidden>
                  <span
                    className={`${styles.fBarFill} ${f.norm === null ? styles.fBarNa : ""}`}
                    style={{ width: `${pct}%` }}
                  />
                </span>
                <span
                  className={styles.fWeight}
                  title={`가중치 ${Math.round(f.weight * 100)}% · 기여 ${
                    contrib === null ? "제외(데이터 없음)" : `${contrib}점`
                  }`}
                >
                  {contrib === null ? "—" : `+${contrib}`}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
