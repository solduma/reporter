"use client";

import Markdown from "@/components/Markdown";
import ValuationCard from "@/components/ValuationCard";
import type { DeepDiveReport, HitlClaim } from "@/lib/types";

import styles from "./DeepDivePanel.module.css";

// valuation 이 신 다중방식 스키마(methods 배열)면 ValuationCard 로, 아니면 구 Section 으로.
export function isMultiMethodValuation(v: unknown): boolean {
  return !!v && typeof v === "object" && Array.isArray((v as { methods?: unknown }).methods);
}

// 값 하나를 사람이 읽는 문자열로. 객체(예 catalysts 항목 {event,impact,source})는 값들을 " · " 로,
// 배열은 각 원소를 재귀 변환해 줄바꿈이 아닌 세미콜론으로 잇는다(원시 JSON 노출 방지).
function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(renderValue).join(" ; ");
  if (typeof v === "object") {
    return Object.values(v as Record<string, unknown>)
      .filter((x) => x !== null && x !== undefined && x !== "")
      .map((x) => (typeof x === "object" ? renderValue(x) : String(x)))
      .join(" · ");
  }
  return String(v);
}

// 구조화 JSON 한 섹션을 키-값 목록으로 렌더(값이 배열·객체면 사람이 읽게 평탄화).
function Section({ title, data }: { title: string; data: Record<string, unknown> | null }) {
  if (!data) {
    return null;
  }
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>{title}</h4>
      <dl className={styles.kv}>
        {Object.entries(data).map(([k, v]) => (
          <div key={k} className={styles.kvRow}>
            <dt className={styles.kvKey}>{k}</dt>
            <dd className={styles.kvVal}>{renderValue(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// verdict → 배지 색 클래스. 반박=회색(반영 안 함), 반영=녹색(100%), 가능성=주황(확률 가중).
function verdictClass(verdict: string): string {
  if (verdict.includes("반박")) return styles.hitlRefute;
  if (verdict.includes("반영")) return styles.hitlReflect;
  return styles.hitlMaybe;
}

// HITL 인풋 검증 결과(반박/반영/가능성) 카드. claims 없으면 렌더 안 함.
function HitlResultCard({ hitl }: { hitl: DeepDiveReport["hitl"] }) {
  const claims = (hitl?.claims ?? []) as HitlClaim[];
  if (!claims.length) {
    return null;
  }
  return (
    <div className={styles.hitlResult}>
      <h4 className={styles.sectionTitle}>사용자 인풋 검증</h4>
      {hitl?.summary ? <p className={styles.hitlSummary}>{hitl.summary}</p> : null}
      {hitl?._procedure_incomplete ? (
        <p className={styles.hitlIncomplete}>
          ⚠️ 일부 수치 인풋의 기준치·환산 절차가 미완료되어 보수적으로 반영됨
        </p>
      ) : null}
      <ul className={styles.hitlClaims}>
        {claims.map((c, i) => (
          <li key={i} className={styles.hitlClaim}>
            <div className={styles.hitlClaimHead}>
              <span className={`${styles.hitlBadge} ${verdictClass(c.verdict)}`}>{c.verdict}</span>
              <span className={styles.hitlProb}>반영 {Math.round((c.probability ?? 0) * 100)}%</span>
              <span className={styles.hitlClaimText}>{c.claim}</span>
            </div>
            {c.numeric && c.claim_type === "numeric" ? (
              <p className={styles.hitlNumeric}>
                현재 {c.numeric.baseline ?? "?"} + 신규 {c.numeric.new_value ?? "?"}
                {c.numeric.unit ?? ""}
                {c.numeric.delta_pct !== null && c.numeric.delta_pct !== undefined
                  ? ` (증분 ${c.numeric.delta_pct}%)`
                  : ""}
                {c.numeric.segment_revenue_share !== null &&
                c.numeric.segment_revenue_share !== undefined
                  ? ` · 매출비중 ${c.numeric.segment_revenue_share}%`
                  : ""}
              </p>
            ) : null}
            {c.valuation_impact ? (
              <p className={styles.hitlImpact}>가정 조정: {c.valuation_impact}</p>
            ) : null}
            {c.evidence ? <p className={styles.hitlEvidence}>근거: {c.evidence}</p> : null}
            {c.reasoning ? <p className={styles.hitlEvidence}>판정: {c.reasoning}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// 딥다이브 보고서 본문(서술·HITL 검증·밸류에이션·단계별 상세). DeepDivePanel 과 공유 페이지가 공유.
export default function DeepDiveReportView({ report }: { report: DeepDiveReport }) {
  return (
    <div className={styles.report}>
      {report.narrative_md ? (
        <div className={styles.narrative}>
          <Markdown content={report.narrative_md} />
        </div>
      ) : null}
      <HitlResultCard hitl={report.hitl} />
      {isMultiMethodValuation(report.valuation) ? (
        <ValuationCard valuation={report.valuation} />
      ) : null}
      <details className={styles.rawDetails}>
        <summary className={styles.rawSummary}>단계별 상세 데이터</summary>
        <Section title="① 기본사항" data={report.overview} />
        <Section title="② 재무 특이점" data={report.redflags} />
        <Section title="③ 사업모델" data={report.business} />
        <Section title="④ 투자 아이디어·리스크" data={report.thesis} />
        {isMultiMethodValuation(report.valuation) ? null : (
          <Section title="⑤ 밸류에이션·결론" data={report.valuation} />
        )}
      </details>
    </div>
  );
}
