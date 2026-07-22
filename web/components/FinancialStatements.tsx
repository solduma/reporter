"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchFinancialStatements } from "@/lib/api";
import type { FinancialStatementPeriod, FinancialStatementsResponse } from "@/lib/types";

import styles from "./FinancialStatements.module.css";

interface Props {
  code: string;
}

type StatementTab = "bs" | "is" | "cf" | "cis";

const STATEMENT_TABS: { key: StatementTab; label: string }[] = [
  { key: "bs", label: "재무상태표" },
  { key: "is", label: "손익계산서" },
  { key: "cf", label: "현금흐름표" },
  { key: "cis", label: "포괄손익계산서" },
];

const STATEMENT_LABELS: Record<StatementTab, string> = {
  bs: "재무상태표",
  is: "손익계산서",
  cf: "현금흐름표",
  cis: "포괄손익계산서",
};

/** 금액 포맷: 억원 단위로 표시. 원 단위 입력 → 억원 변환. */
function formatAmount(amount: number | null): string {
  if (amount === null || amount === undefined) return "—";
  const eok = Math.abs(amount) / 1e8;
  if (eok >= 1) {
    return `${(amount >= 0 ? "" : "-")}${eok.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}억`;
  }
  const man = Math.abs(amount) / 1e4;
  if (man >= 1) {
    return `${(amount >= 0 ? "" : "-")}${man.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}만`;
  }
  return `${amount.toLocaleString()}원`;
}

/** 변동률 포맷 */
function formatChange(pct: number | null): string {
  if (pct === null || pct === undefined) return "";
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${(pct * 100).toFixed(1)}%`;
}

/** 전기 대비 변동률로 그라데이션 opacity 계산. 0%→0, 50%+→1.0 사이 smooth step. */
function changeOpacity(current: number | null, prev: number | null): number {
  if (current === null || prev === null || prev === 0) return 0;
  const pct = Math.abs((current - prev) / prev);
  // 0% → 0, 50% → 1.0, 그 사이는 smooth step
  const t = Math.min(pct, 0.5) / 0.5;
  return t * t * (3 - 2 * t); // smoothstep
}

/** 변동 방향: 1=상승, -1=하락, 0=변화없음 */
function changeDirection(current: number | null, prev: number | null): number {
  if (current === null || prev === null || prev === 0) return 0;
  const pct = (current - prev) / Math.abs(prev);
  if (pct > 0) return 1;
  if (pct < 0) return -1;
  return 0;
}

export default function FinancialStatements({ code }: Props) {
  const [fsDiv, setFsDiv] = useState<"CFS" | "OFS">("CFS");
  const [activeTab, setActiveTab] = useState<StatementTab>("bs");
  const [data, setData] = useState<FinancialStatementsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchFinancialStatements(code, fsDiv);
        if (active) setData(res);
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "재무제표 로드 실패");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [code, fsDiv]);

  // 최신 2개 기간(전기 대비 변동률 계산용)
  const latestPeriods = useMemo(() => {
    if (!data?.periods || data.periods.length < 2) return { current: null, prev: null };
    const ps = data.periods;
    return { current: ps[ps.length - 1], prev: ps[ps.length - 2] };
  }, [data]);

  const currentItems = useMemo(() => {
    if (!latestPeriods.current) return [];
    return latestPeriods.current[activeTab] ?? [];
  }, [latestPeriods, activeTab]);

  const prevItems = useMemo(() => {
    if (!latestPeriods.prev) return [];
    return latestPeriods.prev[activeTab] ?? [];
  }, [latestPeriods, activeTab]);

  // 항목명으로 전기 금액 찾기
  const prevAmount = useCallback(
    (name: string): number | null => {
      const found = prevItems.find((i) => i.name === name);
      return found?.amount ?? null;
    },
    [prevItems],
  );

  // 요약 모드: level 0(대분류)만
  const displayItems = useMemo(() => {
    if (expanded) return currentItems;
    return currentItems.filter((i) => i.level === 0);
  }, [currentItems, expanded]);

  if (loading) {
    return <div className={styles.sectionStatus}>재무제표 불러오는 중…</div>;
  }
  if (error) {
    return <div className={styles.sectionStatus}>{error}</div>;
  }
  if (!data || data.periods.length === 0) {
    return <div className={styles.sectionStatus}>재무제표 데이터가 없습니다</div>;
  }

  const periodLabel = latestPeriods.current?.period ?? "";

  return (
    <div className={styles.container}>
      {/* CFS/OFS 탭 */}
      <div className={styles.fsDivTabs} role="tablist" aria-label="연결/별도 선택">
        {(["CFS", "OFS"] as const).map((div) => (
          <button
            key={div}
            type="button"
            role="tab"
            aria-selected={fsDiv === div}
            className={fsDiv === div ? `${styles.fsDivTab} ${styles.fsDivTabActive}` : styles.fsDivTab}
            onClick={() => setFsDiv(div)}
          >
            {div === "CFS" ? "연결" : "별도"}
          </button>
        ))}
      </div>

      {/* 재무제표 탭 */}
      <div className={styles.statementTabs} role="tablist" aria-label="재무제표 종류">
        {STATEMENT_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab === t.key}
            className={
              activeTab === t.key
                ? `${styles.statementTab} ${styles.statementTabActive}`
                : styles.statementTab
            }
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 테이블 */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.thLeft}>항목</th>
              <th className={styles.thRight}>{periodLabel}</th>
              <th className={styles.thRight}>전기 대비</th>
            </tr>
          </thead>
          <tbody>
            {displayItems.map((item, i) => {
              const prevAmt = prevAmount(item.name);
              const op = changeOpacity(item.amount, prevAmt);
              const dir = changeDirection(item.amount, prevAmt);
              const hlStyle = op > 0
                ? {
                    backgroundColor: dir > 0
                      ? `rgba(18, 138, 77, ${op * 0.15})`
                      : `rgba(192, 43, 43, ${op * 0.15})`,
                    color: dir > 0 ? 'var(--buy)' : 'var(--sell)',
                    fontWeight: op > 0.5 ? 600 : 400,
                  }
                : undefined;
              return (
                <tr
                  key={`${item.account_id}-${i}`}
                  className={
                    item.level === 0 ? styles.rowLevel0 : styles.rowLevel1
                  }
                >
                  <td className={styles.tdLeft}>
                    <span className={item.level === 0 ? styles.nameLevel0 : styles.nameLevel1}>
                      {item.name}
                    </span>
                  </td>
                  <td className={styles.tdRight} style={hlStyle}>
                    {formatAmount(item.amount)}
                  </td>
                  <td className={`${styles.tdRight} ${styles.changeCol}`} style={hlStyle}>
                    {formatChange(
                      item.amount !== null && prevAmt !== null && prevAmt !== 0
                        ? (item.amount - prevAmt) / Math.abs(prevAmt)
                        : null,
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 요약/상세 토글 */}
      <button
        type="button"
        className={styles.toggleBtn}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "▲ 요약 접기" : "▼ 상세 항목 보기"}
      </button>
    </div>
  );
}
