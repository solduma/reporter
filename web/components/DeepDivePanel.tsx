"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import Markdown from "@/components/Markdown";
import ValuationCard from "@/components/ValuationCard";
import { fetchDeepDiveReport, fetchDeepDiveStatus, requestDeepDive } from "@/lib/api";
import type { DeepDiveReport, DeepDiveStatus } from "@/lib/types";

import styles from "./DeepDivePanel.module.css";

// valuation 이 신 다중방식 스키마(methods 배열)면 ValuationCard 로, 아니면 구 Section 으로.
function isMultiMethodValuation(v: unknown): boolean {
  return !!v && typeof v === "object" && Array.isArray((v as { methods?: unknown }).methods);
}

// 5단계 진행 라벨(current_stage 1~5 매핑).
const STAGE_LABELS = ["기본사항", "재무 특이점", "사업모델", "투자 아이디어·리스크", "밸류에이션·결론"];
const POLL_MS = 3000;

// running/pending 상태일 때만 폴링. done/failed/none 이면 멈춘다.
function isActive(status: string): boolean {
  return status === "pending" || status === "running";
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
            <dd className={styles.kvVal}>
              {renderValue(v)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function DeepDivePanel({ code }: { code: string }) {
  const [status, setStatus] = useState<DeepDiveStatus | null>(null);
  const [report, setReport] = useState<DeepDiveReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadReport = useCallback(async () => {
    try {
      setReport(await fetchDeepDiveReport(code));
    } catch {
      /* 보고서 없음은 정상 */
    }
  }, [code]);

  const poll = useCallback(async () => {
    try {
      const s = await fetchDeepDiveStatus(code);
      setStatus(s);
      // running·done 이면 보고서 로드. pending(worker 가 아직 이전 JSON 을 초기화 전)엔 로드 안 함
      // — 재분석 직후 잠깐 옛 결과가 다시 뜨는 깜빡임 방지. running 부터는 '현재 실행' 부분 결과만.
      if (s.has_report && s.status !== "pending") {
        await loadReport();
      }
      if (isActive(s.status)) {
        timerRef.current = setTimeout(poll, POLL_MS);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "상태 조회 실패");
    }
  }, [code, loadReport]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    (async () => {
      const s = await fetchDeepDiveStatus(code).catch(() => null);
      if (!active) {
        return;
      }
      setStatus(s);
      // running·done 이면 로드(진행 중이면 완료 단계까지 실시간 표시). pending 은 로드 안 함
      // (worker 가 이전 단계 JSON 초기화 전이라 옛 결과 깜빡임 방지).
      if (s?.has_report && s.status !== "pending") {
        await loadReport();
      }
      setLoading(false);
      if (s && isActive(s.status)) {
        timerRef.current = setTimeout(poll, POLL_MS);
      }
    })();
    return () => {
      active = false;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
    // poll/loadReport 는 code 로만 바뀌므로 code 의존만으로 충분(폴링 재귀는 자체 setTimeout).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  const onRequest = async () => {
    setRequesting(true);
    setError(null);
    // 재분석 시작 → 이전 보고서·목표가·단계별 상세를 즉시 비운다(진행률만 보이게). 완료 시 새로 로드.
    setReport(null);
    try {
      const s = await requestDeepDive(code);
      setStatus(s);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      setError(e instanceof Error ? e.message : "딥다이브 요청 실패");
    } finally {
      setRequesting(false);
    }
  };

  if (loading) {
    return <div className={styles.status}>불러오는 중…</div>;
  }

  const active = status ? isActive(status.status) : false;
  const stageIdx = status?.current_stage ?? 0;

  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <div className={styles.headInfo}>
          {report?.verdict ? <span className={styles.verdict}>{report.verdict}</span> : null}
          {report?.as_of ? (
            <span className={styles.asOf}>생성 {report.as_of.slice(0, 10)}</span>
          ) : null}
        </div>
        <button type="button" className={styles.runBtn} onClick={onRequest} disabled={active || requesting}>
          {active ? "분석 진행 중…" : report ? "다시 분석" : "딥다이브 실행"}
        </button>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}

      {active ? (
        <div className={styles.progress}>
          <div className={styles.progressBar}>
            <div className={styles.progressFill} style={{ width: `${status?.progress ?? 0}%` }} />
          </div>
          <div className={styles.stages}>
            {STAGE_LABELS.map((label, i) => (
              <span
                key={label}
                className={
                  i < stageIdx ? `${styles.stage} ${styles.stageDone}` : i === stageIdx ? `${styles.stage} ${styles.stageActive}` : styles.stage
                }
              >
                {i + 1}. {label}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {status?.status === "failed" ? (
        <p className={styles.error}>딥다이브 실패: {status.error ?? "알 수 없는 오류"}</p>
      ) : null}

      {status?.status === "none" && !report ? (
        <p className={styles.empty}>아직 딥다이브 보고서가 없습니다. 실행하면 5단계 심층 분석 보고서를 생성합니다.</p>
      ) : null}

      {report ? (
        <div className={styles.report}>
          {report.narrative_md ? (
            <div className={styles.narrative}>
              <Markdown content={report.narrative_md} />
            </div>
          ) : null}
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
      ) : null}
    </div>
  );
}
