"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import Markdown from "@/components/Markdown";
import DeepDiveReportView from "@/components/DeepDiveReportView";
import ShareLinkButton from "@/components/ShareLinkButton";
import {
  fetchDeepDiveReport,
  fetchDeepDiveStatus,
  requestDeepDive,
  submitDeepDiveHitl,
} from "@/lib/api";
import type { DeepDiveReport, DeepDiveStatus } from "@/lib/types";

import styles from "./DeepDivePanel.module.css";

// 5단계 진행 라벨(current_stage 1~5 매핑).
const STAGE_LABELS = ["기본사항", "재무 특이점", "사업모델", "투자 아이디어·리스크", "밸류에이션·결론"];
const POLL_MS = 3000;

// running/pending 상태일 때만 폴링. done/failed/none 이면 멈춘다.
function isActive(status: string): boolean {
  return status === "pending" || status === "running";
}

export default function DeepDivePanel({ code }: { code: string }) {
  const [status, setStatus] = useState<DeepDiveStatus | null>(null);
  const [report, setReport] = useState<DeepDiveReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hitlInput, setHitlInput] = useState("");
  const [hitlSubmitting, setHitlSubmitting] = useState(false);
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

  // 밸류에이션 직전 HITL 인풋 제출(빈 값이면 건너뜀). 재개 → 폴링 재시작.
  const onSubmitHitl = async (skip: boolean) => {
    setHitlSubmitting(true);
    setError(null);
    try {
      const s = await submitDeepDiveHitl(code, skip ? "" : hitlInput);
      setStatus(s);
      setHitlInput("");
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      setError(e instanceof Error ? e.message : "HITL 인풋 제출 실패");
    } finally {
      setHitlSubmitting(false);
    }
  };

  if (loading) {
    return <div className={styles.status}>불러오는 중…</div>;
  }

  const active = status ? isActive(status.status) : false;
  const paused = status?.status === "paused";
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
        <div className={styles.headActions}>
          {/* 딥다이브 완료 시 주담 전략(별도 호흡) 전용 페이지로 이동. */}
          {report && !active && !paused ? (
            <Link href={`/ir-interview/${code}`} className={styles.irLink}>
              주담 전략 →
            </Link>
          ) : null}
          {report && !active && !paused ? <ShareLinkButton code={code} /> : null}
          <button type="button" className={styles.runBtn} onClick={onRequest} disabled={active || paused || requesting}>
            {active ? "분석 진행 중…" : paused ? "인풋 대기 중…" : report ? "다시 분석" : "딥다이브 실행"}
          </button>
        </div>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}

      {paused ? (
        <div className={styles.hitl}>
          <div className={styles.hitlPrompt}>
            <Markdown content={status?.hitl_prompt ?? "밸류에이션 직전입니다. 추가 정보를 입력하세요."} />
          </div>
          <textarea
            className={styles.hitlInput}
            value={hitlInput}
            onChange={(e) => setHitlInput(e.target.value)}
            placeholder="예: 이번 분기 대형 데이터센터 수주가 임박했다 / 무형자산 손상 우려가 있다"
            rows={3}
            disabled={hitlSubmitting}
          />
          <div className={styles.hitlActions}>
            <button
              type="button"
              className={styles.runBtn}
              onClick={() => onSubmitHitl(false)}
              disabled={hitlSubmitting || !hitlInput.trim()}
            >
              {hitlSubmitting ? "검증 중…" : "인풋 반영하고 계속"}
            </button>
            <button
              type="button"
              className={styles.hitlSkip}
              onClick={() => onSubmitHitl(true)}
              disabled={hitlSubmitting}
            >
              건너뛰고 계속
            </button>
          </div>
        </div>
      ) : null}

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

      {report ? <DeepDiveReportView report={report} /> : null}
    </div>
  );
}
