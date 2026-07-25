"use client";

import { useCallback, useEffect, useState } from "react";

import Markdown from "@/components/Markdown";
import { fetchBusinessOverview, refreshBusinessOverview } from "@/lib/api";
import type { BusinessOverview as BusinessOverviewType, BusinessTable } from "@/lib/types";

import styles from "./BusinessOverview.module.css";

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
    </div>
  );
}