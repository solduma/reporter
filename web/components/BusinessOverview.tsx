"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import Markdown from "@/components/Markdown";
import {
  fetchBusinessOverview,
  refreshBusinessOverview,
  requestBusinessResearch,
  fetchBusinessResearchStatus,
} from "@/lib/api";
import type { BusinessOverview as BusinessOverviewType, BusinessTable, ResearchStatus, ResearchSummary } from "@/lib/types";

import styles from "./BusinessOverview.module.css";

const POLL_MS = 3000;

// 섹션 id → 표시 라벨(LLM 이 title 을 비워도 표시용 확보).
const SECTION_LABELS: Record<string, string> = {
  business_summary: "사업 개요",
  main_products: "주요 제품·서비스",
  market_risk: "시장·가격 위험",
  raw_materials: "원재료",
  production: "생산·설비",
  sales: "판매",
  ownership: "주주구성",
  recent_updates: "최근 경영사항",
};

const KIND_LABEL: Record<string, string> = {
  annual: "사업",
  half: "반기",
  quarter: "분기",
};

function sectionTitle(id: string, title: string): string {
  return title || SECTION_LABELS[id] || id;
}

function BusinessTableView({ table }: { table: BusinessTable }) {
  if (table.rows.length === 0 && table.headers.length === 0) return null;
  return (
    <div className={styles.tableWrap}>
      {table.title ? <p className={styles.tableTitle}>{table.title}</p> : null}
      <div className={styles.scroll}>
        <table className={styles.table}>
          {table.headers.length > 0 ? (
            <thead>
              <tr>
                {table.headers.map((h, i) => (
                  <th key={i} scope="col">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function BusinessOverview({ code }: { code: string }) {
  const [state, setState] = useState<
    { status: "loading" | "ready" | "error"; data: BusinessOverviewType | null; message?: string }
  >({ status: "loading", data: null });
  const [refreshing, setRefreshing] = useState(false);

  // Research+ 상태
  const [researchStatus, setResearchStatus] = useState<ResearchStatus | null>(null);
  const [guidelineInput, setGuidelineInput] = useState("");
  const [requesting, setRequesting] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setState({ status: "loading", data: null });
    try {
      const res = await fetchBusinessOverview(code);
      setState({ status: "ready", data: res });
    } catch (e) {
      setState({
        status: "error",
        data: null,
        message: e instanceof Error ? e.message : "사업 개요를 불러오지 못했습니다",
      });
    }
  }, [code]);

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      const res = await refreshBusinessOverview(code);
      setState({ status: "ready", data: res });
    } catch (e) {
      setState({
        status: "error",
        data: state.data,
        message: e instanceof Error ? e.message : "사업 개요 갱신 실패",
      });
    } finally {
      setRefreshing(false);
    }
  };

  // Research+ 요청
  const onRequestResearch = async () => {
    setRequesting(true);
    try {
      const status = await requestBusinessResearch(code, guidelineInput);
      setResearchStatus(status);
    } catch (e) {
      console.error("Research request failed:", e);
    } finally {
      setRequesting(false);
    }
  };

  // Research+ 상태 폴링
  const pollResearch = useCallback(async () => {
    try {
      const status = await fetchBusinessResearchStatus(code);
      setResearchStatus(status);
      // 완료되면 overview 재로드 (research_summary 반영)
      if (status.status === "done" && state.status === "ready") {
        await load();
      }
      // pending|running이면 계속 폴링
      if (status.status === "pending" || status.status === "running") {
        pollTimerRef.current = setTimeout(pollResearch, POLL_MS);
      }
    } catch (e) {
      console.error("Research status poll failed:", e);
    }
  }, [code, load, state.status]);

  // researchStatus.status 변화 시 폴링 시작/정지
  useEffect(() => {
    if (researchStatus?.status === "pending" || researchStatus?.status === "running") {
      pollTimerRef.current = setTimeout(pollResearch, POLL_MS);
    }
    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [researchStatus?.status, pollResearch]);

  // 초기 로드 시 research summary 있으면 상태도 가져옴
  useEffect(() => {
    if (state.status === "ready" && state.data?.research_summary) {
      setResearchStatus({
        stock_code: code,
        status: "done",
        progress: 100,
        error: null,
        has_summary: true,
      });
    }
  }, [state.status, state.data, code]);

  if (state.status === "loading") {
    return <div className={styles.status}>불러오는 중…</div>;
  }
  if (state.status === "error" && !state.data) {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }

  const ov = state.data;
  if (!ov || ov.sections.length === 0) {
    // 미조립(사업보고서 없음·LLM 미설정). 수동 갱신으로 재시도 유도.
    return (
      <div className={styles.empty}>
        <p className={styles.emptyText}>
          사업 개요가 아직 없습니다. 사업보고서 기반으로 정리하려면 갱신을 누르세요.
        </p>
        <button type="button" className={styles.refreshBtn} onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "정리 중…" : "사업 개요 생성"}
        </button>
        {state.message ? <p className={styles.error}>{state.message}</p> : null}
      </div>
    );
  }

  const base = ov.source_reports.find((r) => r.is_base);
  const updates = ov.source_reports.filter((r) => !r.is_base);

  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <div className={styles.sources}>
          {base ? (
            <span className={styles.sourceBase}>
              기준 {KIND_LABEL[base.kind] ?? base.kind} {base.period}
            </span>
          ) : null}
          {updates.length > 0 ? (
            <span className={styles.sourceUpdates}>
              반영 {updates.map((u) => `${KIND_LABEL[u.kind] ?? u.kind} ${u.period}`).join(" · ")}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          className={styles.refreshBtn}
          onClick={onRefresh}
          disabled={refreshing}
          title="새 정기보고서 반영·재정리"
        >
          {refreshing ? "정리 중…" : "갱신"}
        </button>
      </div>

      {state.status === "error" ? <p className={styles.error}>{state.message}</p> : null}

      <div className={styles.sections}>
        {ov.sections.map((sec) => {
          const hasContent = sec.narrative || sec.tables.some((t) => t.rows.length > 0);
          if (!hasContent) return null; // 빈 섹션은 숨김(정보 없음 항목 노출 최소화)
          return (
            <section key={sec.id} className={styles.section}>
              <div className={styles.sectionHead}>
                <h3 className={styles.sectionTitle}>{sectionTitle(sec.id, sec.title)}</h3>
                {sec.updated_by_kind && sec.updated_by_kind !== "annual" ? (
                  <span className={styles.updatedTag}>
                    {KIND_LABEL[sec.updated_by_kind] ?? sec.updated_by_kind} 갱신
                  </span>
                ) : null}
              </div>
              {sec.narrative ? <Markdown content={sec.narrative} /> : null}
              {sec.tables.map((t, i) => (
                <BusinessTableView key={i} table={t} />
              ))}
            </section>
          );
        })}
      </div>

      {/* Research+ 패널 */}
      <div className={styles.researchBox}>
        <div className={styles.researchHead}>
          <h3 className={styles.sectionTitle}>Research+</h3>
          {researchStatus?.status === "done" && researchStatus.has_summary ? (
            <span className={styles.updatedTag}>완료</span>
          ) : null}
        </div>

        {/* 가이드라인 입력 + 요청 버튼 */}
        <div className={styles.researchInput}>
          <textarea
            className={styles.guidelineInput}
            placeholder="리서치 방향(선택): 공급망, 고객사, 경쟁환경, 밸류체인 등 중점 분석 항목을 지시하세요."
            value={guidelineInput}
            onChange={(e) => setGuidelineInput(e.target.value)}
            rows={2}
            disabled={requesting || researchStatus?.status === "running"}
          />
          <button
            type="button"
            className={styles.refreshBtn}
            onClick={onRequestResearch}
            disabled={requesting || !guidelineInput.trim() || researchStatus?.status === "running"}
          >
            {requesting || researchStatus?.status === "running" ? "분석 중…" : "리서치+"}
          </button>
        </div>

        {/* 상태 메시지 */}
        {researchStatus?.status === "failed" && researchStatus.error ? (
          <p className={styles.error}>{researchStatus.error}</p>
        ) : null}
        {researchStatus?.status === "running" ? (
          <p className={styles.researchProgress}>
            진행률 {researchStatus.progress}% — LLM이 공시·웹 자료를 분석 중입니다…
          </p>
        ) : null}

        {/* 결과 렌더 */}
        {researchStatus?.status === "done" && ov.research_summary ? (
          <div className={styles.researchResult}>
            <ResearchResultView summary={ov.research_summary} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

// Research+ 결과 렌더 컴포넌트
function ResearchResultView({ summary }: { summary: ResearchSummary }) {
  return (
    <>
      {/* 엔티티 테이블 */}
      {(summary.vendors.length > 0 || summary.customers.length > 0 || summary.competitors.length > 0) && (
        <div className={styles.researchSection}>
          <h4>공급망·고객·경쟁</h4>
          {summary.vendors.length > 0 ? (
            <div className={styles.entityBlock}>
              <h5>주요 공급자</h5>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>공급자</th>
                    <th>역할</th>
                    <th>비고</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.vendors.map((v, i) => (
                    <tr key={i}>
                      <td>{v.name}</td>
                      <td>{v.role}</td>
                      <td>{v.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {summary.customers.length > 0 ? (
            <div className={styles.entityBlock}>
              <h5>주요 고객</h5>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>고객</th>
                    <th>역할</th>
                    <th>비고</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.customers.map((c, i) => (
                    <tr key={i}>
                      <td>{c.name}</td>
                      <td>{c.role}</td>
                      <td>{c.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {summary.competitors.length > 0 ? (
            <div className={styles.entityBlock}>
              <h5>경쟁사</h5>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>경쟁사</th>
                    <th>경쟁 양상</th>
                    <th>비고</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.competitors.map((c, i) => (
                    <tr key={i}>
                      <td>{c.name}</td>
                      <td>{c.role}</td>
                      <td>{c.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      )}

      {/* 밸류체인 */}
      {summary.value_chain.length > 0 && (
        <div className={styles.researchSection}>
          <h4>밸류체인</h4>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>단계</th>
                <th>방향</th>
                <th>관계 대상</th>
                <th>비고</th>
              </tr>
            </thead>
            <tbody>
              {summary.value_chain.map((link, i) => (
                <tr key={i}>
                  <td>{link.stage}</td>
                  <td>{link.direction === "upstream" ? "업스트림" : "다운스트림"}</td>
                  <td>{link.entity}</td>
                  <td>{link.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 서술 */}
      {summary.narrative_md ? (
        <div className={styles.researchSection}>
          <h4>종합 분석</h4>
          <Markdown content={summary.narrative_md} />
        </div>
      ) : null}
    </>
  );
}