"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchFinancialStatements } from "@/lib/api";
import type { FinancialStatementItem as FSItem, FinancialStatementsResponse } from "@/lib/types";

import styles from "./FinancialStatements.module.css";

interface Props {
  code: string;
}

type StatementTab = "bs" | "is" | "cf" | "equity";

const STATEMENT_TABS: { key: StatementTab; label: string }[] = [
  { key: "bs", label: "재무상태표" },
  { key: "is", label: "손익계산서" },
  { key: "cf", label: "현금흐름표" },
  { key: "equity", label: "자본변동표" },
];

type AmountUnit = { divisor: number; suffix: string; decimals: number };

const UNITS: AmountUnit[] = [
  { divisor: 1e8, suffix: "억", decimals: 1 },
  { divisor: 1e4, suffix: "만", decimals: 0 },
];

/** 금액 포맷: 테이블 단위(divisor)로 변환, 숫자-단위 사이 공백. */
function formatAmount(amount: number | null, divisor: number, suffix: string, decimals: number): string {
  if (amount === null || amount === undefined) return "—";
  const abs = Math.abs(amount);
  const sign = amount >= 0 ? "" : "-";
  return `${sign}${(abs / divisor).toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} ${suffix}`;
}

/** 테이블 전체 금액 중 최대 절대값을 보고 공통 단위 결정. */
function resolveAmountUnit(maxAbs: number): AmountUnit {
  for (const unit of UNITS) {
    if (maxAbs >= unit.divisor) return unit;
  }
  return { divisor: 1, suffix: "원", decimals: 0 };
}

function collectAmounts(items: FSItem[]): number[] {
  const amounts: number[] = [];
  for (const item of items) {
    if (item.amount !== null && item.amount !== undefined) amounts.push(Math.abs(item.amount));
    if (item.prev_amount !== null && item.prev_amount !== undefined) amounts.push(Math.abs(item.prev_amount));
    amounts.push(...collectAmounts(item.children));
  }
  return amounts;
}

/** 변동률 포맷 */
function formatChange(pct: number | null): string {
  if (pct === null || pct === undefined) return "";
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${(pct * 100).toFixed(1)}%`;
}

/** 전기 대비 변동률로 그라데이션 opacity. 0%→0, 50%+→1.0 smoothstep. */
function changeOpacity(current: number | null, prev: number | null): number {
  if (current === null || prev === null || prev === 0) return 0;
  const pct = Math.abs((current - prev) / prev);
  const t = Math.min(pct, 0.5) / 0.5;
  return t * t * (3 - 2 * t);
}

function changeDirection(current: number | null, prev: number | null): number {
  if (current === null || prev === null || prev === 0) return 0;
  return (current - prev) / Math.abs(prev) > 0 ? 1 : -1;
}

/** 재무제표 행: level에 따라 스타일·들여쓰기, children은 재귀 렌더링. */
function ItemRow({
  item,
  unit,
  defaultOpen = false,
}: {
  item: FSItem;
  unit: AmountUnit;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hasChildren = item.children && item.children.length > 0;
  const op = changeOpacity(item.amount, item.prev_amount);
  const dir = changeDirection(item.amount, item.prev_amount);
  const hlStyle = op > 0
    ? {
        backgroundColor: dir > 0
          ? `rgba(18, 138, 77, ${op * 0.15})`
          : `rgba(192, 43, 43, ${op * 0.15})`,
        color: dir > 0 ? "var(--buy)" : "var(--sell)",
        fontWeight: op > 0.5 ? 600 : 400,
      }
    : undefined;

  const isLevel0 = item.level === 0;
  const namePaddingLeft = isLevel0 ? 0 : `${item.level * 0.75}rem`;
  return (
    <>
      <tr className={isLevel0 ? styles.rowLevel0 : styles.rowLevel1}>
        <td className={styles.tdLeft}>
          {hasChildren ? (
            <button
              type="button"
              className={styles.expandBtn}
              onClick={() => setOpen((v) => !v)}
              aria-label={open ? "접기" : "펼치기"}
            >
              <span className={open ? styles.arrowDown : styles.arrowRight}>▶</span>
            </button>
          ) : (
            <span className={styles.expandPlaceholder} />
          )}
          <span
            className={isLevel0 ? styles.nameLevel0 : styles.nameLevel1}
            style={{ paddingLeft: namePaddingLeft }}
          >
            {item.name}
          </span>
        </td>
        <td className={styles.tdRight}>
          {formatAmount(item.amount, unit.divisor, unit.suffix, unit.decimals)}
        </td>
        <td className={styles.tdRight}>
          {formatAmount(item.prev_amount, unit.divisor, unit.suffix, unit.decimals)}
        </td>
        <td className={`${styles.tdRight} ${styles.changeCol}`} style={hlStyle}>
          {formatChange(
            item.amount !== null && item.prev_amount !== null && item.prev_amount !== 0
              ? (item.amount - item.prev_amount) / Math.abs(item.prev_amount)
              : null,
          )}
        </td>
      </tr>
      {open && hasChildren
        ? item.children.map((child, ci) => (
            <ItemRow
              key={`${child.account_id}-${ci}`}
              item={child}
              unit={unit}
              defaultOpen={defaultOpen}
            />
          ))
        : null}
    </>
  );
}

export default function FinancialStatements({ code }: Props) {
  const [fsDiv, setFsDiv] = useState<"CFS" | "OFS">("CFS");
  const [activeTab, setActiveTab] = useState<StatementTab>("bs");
  const [data, setData] = useState<FinancialStatementsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const latestPeriod = useMemo(() => {
    if (!data?.periods || data.periods.length === 0) return null;
    return data.periods[data.periods.length - 1];
  }, [data]);

  const items = useMemo(() => {
    if (!latestPeriod) return [];
    return latestPeriod[activeTab] ?? [];
  }, [latestPeriod, activeTab]);

  const amountUnit = useMemo(() => {
    const values = collectAmounts(items);
    const maxAbs = values.length > 0 ? Math.max(...values) : 0;
    return resolveAmountUnit(maxAbs);
  }, [items]);

  if (loading) {
    return <div className={styles.sectionStatus}>재무제표 불러오는 중…</div>;
  }
  if (error) {
    return <div className={styles.sectionStatus}>{error}</div>;
  }
  if (!data || data.periods.length === 0) {
    return <div className={styles.sectionStatus}>재무제표 데이터가 없습니다</div>;
  }

  const periodLabel = latestPeriod?.period ?? "";
  const prevPeriodLabel = latestPeriod?.prev_period ?? null;

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
              <th className={styles.thRight}>{prevPeriodLabel ?? "전기"}</th>
              <th className={styles.thRight}>변동률</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <ItemRow
                key={`${item.account_id}-${i}`}
                item={item}
                unit={amountUnit}
                defaultOpen={activeTab === "bs"}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
