"use client";

import Markdown from "@/components/Markdown";
import type { ValuationMethod, ValuationResult } from "@/lib/types";

import styles from "./ValuationCard.module.css";

// valuation JSON 이 신 다중방식 스키마(methods 배열)인지 판별.
function isMultiMethod(v: unknown): v is ValuationResult {
  return !!v && typeof v === "object" && Array.isArray((v as ValuationResult).methods);
}

function fmtWon(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n).toLocaleString("ko-KR")}원`;
}

function fmtPct(n: number | null): string {
  if (n === null || n === undefined) return "";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

// 업사이드 방향에 따른 색(상승=매수색, 하락=매도색).
function upsideClass(n: number | null): string {
  if (n === null || n === undefined) return "";
  return n >= 0 ? styles.up : styles.down;
}

const CONF_ORDER: Record<string, number> = { 상: 0, 중: 1, 하: 2 };

// 가정 dict 를 사람이 읽는 라벨로(핵심만; 전체는 process 스텝이 설명).
function AssumptionChips({ a }: { a: Record<string, unknown> }) {
  const entries = Object.entries(a).filter(([, v]) => typeof v === "number" || typeof v === "string");
  if (entries.length === 0) return null;
  return (
    <div className={styles.chips}>
      {entries.map(([k, v]) => (
        <span key={k} className={styles.chip}>
          <span className={styles.chipKey}>{k}</span> {String(v)}
        </span>
      ))}
    </div>
  );
}

function MethodRow({ m }: { m: ValuationMethod }) {
  // 종목 유형 부적합·이상치로 최종 평균에서 빠진 방식은 note 에 '제외'가 담긴다 → 배지로 표시.
  const excluded = typeof m.note === "string" && m.note.includes("제외");
  return (
    <details className={`${styles.method} ${excluded ? styles.methodExcluded : ""}`}>
      <summary className={styles.methodSummary}>
        <span className={styles.methodName}>
          {m.label}
          {excluded ? <span className={styles.excludedTag}>제외</span> : null}
        </span>
        <span className={styles.methodTarget}>
          {m.applicable ? (
            <>
              {fmtWon(m.target_price)}
              {m.upside_pct !== null ? (
                <em className={`${styles.methodUpside} ${upsideClass(m.upside_pct)}`}>{fmtPct(m.upside_pct)}</em>
              ) : null}
            </>
          ) : (
            <span className={styles.na}>적용 불가</span>
          )}
        </span>
        <span className={`${styles.conf} ${styles[`conf${m.confidence}`] ?? ""}`}>{m.confidence}</span>
      </summary>
      <div className={styles.methodBody}>
        {m.process.length > 0 ? (
          <ol className={styles.process}>
            {m.process.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        ) : null}
        {m.note ? <p className={styles.note}>{m.note}</p> : null}
        <AssumptionChips a={m.assumptions} />
      </div>
    </details>
  );
}

// 다중 밸류에이션 카드: 최종 목표가(상단 강조) + 방식별 collapse 목록.
export default function ValuationCard({ valuation }: { valuation: unknown }) {
  if (!isMultiMethod(valuation)) {
    return null; // 구 스키마는 상위(DeepDivePanel)가 Section 으로 렌더.
  }
  const v = valuation;
  const methods = [...v.methods].sort((a, b) => {
    if (a.applicable !== b.applicable) return a.applicable ? -1 : 1; // 적용 가능 먼저
    return (CONF_ORDER[a.confidence] ?? 1) - (CONF_ORDER[b.confidence] ?? 1); // 신뢰도 높은 순
  });

  return (
    <div className={styles.card}>
      <div className={styles.finalRow}>
        <div className={styles.finalMain}>
          <span className={styles.finalLabel}>최종 목표가</span>
          <span className={styles.finalPrice}>{fmtWon(v.final_target_price)}</span>
          {v.final_upside_pct !== null ? (
            <span className={`${styles.finalUpside} ${upsideClass(v.final_upside_pct)}`}>
              {fmtPct(v.final_upside_pct)}
            </span>
          ) : null}
        </div>
        <div className={styles.finalMeta}>
          {v.current_price !== null ? <span>현재가 {fmtWon(v.current_price)}</span> : null}
          {v.entry_case ? <span className={styles.entryCase}>{v.entry_case}</span> : null}
          <span>{v.method_count}개 방식 종합</span>
        </div>
      </div>

      {v.conclusion ? (
        <div className={styles.conclusion}>
          <Markdown content={v.conclusion} />
        </div>
      ) : null}

      <div className={styles.methods}>
        {methods.map((m) => (
          <MethodRow key={m.method} m={m} />
        ))}
      </div>
    </div>
  );
}
