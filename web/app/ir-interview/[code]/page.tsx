"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import IrInterviewView from "@/components/IrInterviewView";
import {
  deleteIrInterview,
  fetchIrInterviewReport,
  fetchIrInterviewStatus,
  requestIrInterview,
} from "@/lib/api";
import type { IrInterviewReport, IrInterviewStatus } from "@/lib/types";

import styles from "../page.module.css";

export default function IrInterviewCodePage({ params }: { params: { code: string } }) {
  const { code } = params;
  const router = useRouter();
  const [report, setReport] = useState<IrInterviewReport | null>(null);
  const [status, setStatus] = useState<IrInterviewStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const loadReport = useCallback(async () => {
    try {
      const r = await fetchIrInterviewReport(code);
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "결과를 불러오지 못했습니다");
    }
  }, [code]);

  // 상태 폴링 — running 이면 완료까지 재확인, 완료 시 결과 재로드.
  const poll = useCallback(async () => {
    try {
      const s = await fetchIrInterviewStatus(code);
      setStatus(s);
      if (s.status === "running" || s.status === "pending") {
        timer.current = setTimeout(poll, 5000);
      } else if (s.status === "done") {
        await loadReport();
      }
    } catch {
      // 상태 조회 실패는 무시(다음 액션에서 재시도).
    }
  }, [code, loadReport]);

  useEffect(() => {
    let active = true;
    (async () => {
      await loadReport();
      if (active) {
        setLoading(false);
        void poll();
      }
    })();
    return () => {
      active = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadReport, poll]);

  const onGenerate = async () => {
    setError(null);
    try {
      const s = await requestIrInterview(code);
      setStatus(s);
      void poll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "주담 전략 생성 요청 실패");
    }
  };

  const onDelete = async () => {
    if (!window.confirm("이 주담 전략을 삭제할까요?")) return;
    setDeleting(true);
    try {
      await deleteIrInterview(code);
      router.push("/ir-interview");
    } catch (e) {
      setError(e instanceof Error ? e.message : "삭제 실패");
      setDeleting(false);
    }
  };

  const active = status?.status === "running" || status?.status === "pending";
  const hasStrategy = !!report?.strategy?.strategy_items?.length;

  return (
    <main className={styles.page}>
      <div className={styles.headerRow}>
        <div>
          <h1 className={styles.title}>{report?.stock_name ?? code} · 주담 인터뷰 전략</h1>
          <p className={styles.sub}>
            <Link href={`/companies/${code}`} className={styles.backLink}>
              ← 종목 분석
            </Link>
            {report?.as_of ? <span> · 생성 {report.as_of.slice(0, 10)}</span> : null}
          </p>
        </div>
        <div className={styles.headerActions}>
          <button type="button" className={styles.genBtn} onClick={onGenerate} disabled={active}>
            {active ? "생성 중…" : hasStrategy ? "다시 생성" : "주담 전략 생성"}
          </button>
          {hasStrategy ? (
            <button
              type="button"
              className={styles.delBtn}
              onClick={onDelete}
              disabled={deleting || active}
            >
              {deleting ? "삭제 중…" : "삭제"}
            </button>
          ) : null}
        </div>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}
      {active ? (
        <p className={styles.status}>
          주담 인터뷰 전략을 생성 중입니다(수 분 소요). 전략 아이템 도출 → 아이템별 질문 검증…
        </p>
      ) : null}

      {loading ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : hasStrategy ? (
        <IrInterviewView strategy={report!.strategy!} />
      ) : !active ? (
        <p className={styles.status}>
          아직 생성된 전략이 없습니다. 딥다이브 완료 후 &lsquo;주담 전략 생성&rsquo;을 눌러 만드세요.
        </p>
      ) : null}
    </main>
  );
}
