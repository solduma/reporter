"use client";

import type { IrInterviewStrategy } from "@/lib/types";

import styles from "./IrInterviewView.module.css";

// 주담 인터뷰 전략 결과 — 전략 아이템별로 왜 중요한지 + 밸류 가정 연결 + 질문 목록(의미 설명 포함).
export default function IrInterviewView({ strategy }: { strategy: IrInterviewStrategy }) {
  const items = strategy.strategy_items ?? [];
  return (
    <div className={styles.wrap}>
      <p className={styles.total}>
        전략 아이템 {items.length}개 · 질문 {strategy.total_questions}개
      </p>
      {items.map((it, idx) => (
        <section key={idx} className={styles.item}>
          <div className={styles.itemHead}>
            <h3 className={styles.itemTitle}>{it.item}</h3>
            <span className={styles.qCount}>질문 {it.questions?.length ?? 0}</span>
          </div>
          {it.why_matters ? <p className={styles.why}>왜 중요: {it.why_matters}</p> : null}
          {it.linked_valuation_assumption ? (
            <p className={styles.link}>연결 밸류 가정: {it.linked_valuation_assumption}</p>
          ) : null}
          <ol className={styles.questions}>
            {(it.questions ?? []).map((q, qi) => (
              <li key={qi} className={styles.question}>
                <p className={styles.q}>{q.q}</p>
                <dl className={styles.qMeta}>
                  {q.intent ? (
                    <div className={styles.qMetaRow}>
                      <dt>의도</dt>
                      <dd>{q.intent}</dd>
                    </div>
                  ) : null}
                  {q.valuation_link ? (
                    <div className={styles.qMetaRow}>
                      <dt>밸류 연결</dt>
                      <dd>{q.valuation_link}</dd>
                    </div>
                  ) : null}
                  {q.expected_signal ? (
                    <div className={styles.qMetaRow}>
                      <dt>예상 시그널</dt>
                      <dd>{q.expected_signal}</dd>
                    </div>
                  ) : null}
                </dl>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}
