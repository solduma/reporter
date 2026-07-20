"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchUsScreener } from "@/lib/api";
import type { UsScreenerQuery, UsScreenerRow } from "@/lib/types";

import styles from "./page.module.css";

// USD 시총·거래대금 축약.
function usd(v: number | null): string {
  if (v === null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

function n2(v: number | null, suffix = ""): string {
  return v === null ? "—" : `${v}${suffix}`;
}

const SORTS: { key: string; label: string }[] = [
  { key: "score", label: "스코어" },
  { key: "market_cap", label: "시총" },
  { key: "per", label: "저PER" },
  { key: "momentum", label: "모멘텀" },
  { key: "trading_value", label: "거래대금" },
];

const PAGE_SIZE = 50;

export default function UsScreenerPage() {
  const [rows, setRows] = useState<UsScreenerRow[]>([]);
  const [total, setTotal] = useState(0);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 필터 상태.
  const [sort, setSort] = useState("score");
  const [perMax, setPerMax] = useState<number | undefined>(undefined);
  const [exchange, setExchange] = useState<"" | "NASDAQ" | "NYSE">("");
  const [hasEvent, setHasEvent] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    const query: UsScreenerQuery = {
      sort,
      perMax,
      exchange: exchange || undefined,
      hasEvent: hasEvent || undefined,
      limit: PAGE_SIZE,
      offset,
    };
    void fetchUsScreener(query)
      .then((r) => {
        if (!active) return;
        setRows(r.items);
        setTotal(r.total);
        setAsOf(r.as_of);
      })
      .catch(() => active && setError("US 스크리너를 불러오지 못했습니다"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [sort, perMax, exchange, hasEvent, offset]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>US 스크리너</h1>
        <span className={styles.tag}>S&amp;P500 + 나스닥 + 성장주 · SEC/네이버</span>
        {asOf ? <span className={styles.asof}>{asOf} 기준</span> : null}
      </header>

      <div className={styles.filters}>
        <label className={styles.filter}>
          정렬
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.filter}>
          거래소
          <select value={exchange} onChange={(e) => setExchange(e.target.value as "" | "NASDAQ" | "NYSE")}>
            <option value="">전체</option>
            <option value="NASDAQ">NASDAQ</option>
            <option value="NYSE">NYSE</option>
          </select>
        </label>
        <label className={styles.filter}>
          PER 상한
          <select
            value={perMax ?? ""}
            onChange={(e) => setPerMax(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">제한 없음</option>
            <option value="10">10 이하</option>
            <option value="20">20 이하</option>
            <option value="30">30 이하</option>
          </select>
        </label>
        <label className={styles.checkbox}>
          <input type="checkbox" checked={hasEvent} onChange={(e) => setHasEvent(e.target.checked)} />
          최근 8-K 있는 종목만
        </label>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}

      <section className={styles.card}>
        <div className={styles.tableHead}>
          <span className={styles.count}>{loading ? "불러오는 중…" : `${total}종목`}</span>
        </div>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>종목</th>
              <th className={styles.num}>스코어</th>
              <th>거래소</th>
              <th className={styles.num}>현재가</th>
              <th className={styles.num}>등락</th>
              <th className={styles.num}>시총</th>
              <th className={styles.num}>PER</th>
              <th className={styles.num}>PBR</th>
              <th className={styles.num}>모멘텀</th>
              <th>8-K</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker}>
                <td>
                  <Link href={`/us/${r.ticker}`} className={styles.tickerLink}>
                    <span className={styles.ticker}>{r.ticker}</span>
                    <span className={styles.name}>{r.name}</span>
                  </Link>
                </td>
                <td className={`${styles.num} ${styles.score}`}>{n2(r.score)}</td>
                <td className={styles.exch}>{r.exchange ?? "—"}</td>
                <td className={styles.num}>{r.close_price !== null ? `$${r.close_price}` : "—"}</td>
                <td className={`${styles.num} ${(r.change_pct ?? 0) >= 0 ? styles.up : styles.down}`}>
                  {r.change_pct !== null ? `${r.change_pct > 0 ? "+" : ""}${r.change_pct}%` : "—"}
                </td>
                <td className={styles.num}>{usd(r.market_cap)}</td>
                <td className={styles.num}>{n2(r.per)}</td>
                <td className={styles.num}>{n2(r.pbr)}</td>
                <td className={styles.num}>{n2(r.momentum_3m, "%")}</td>
                <td>{r.has_recent_8k ? <span className={styles.badge}>NEW</span> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length === 0 ? (
          <p className={styles.empty}>조건에 맞는 종목이 없습니다</p>
        ) : null}

        {/* 페이지네이션 */}
        {total > PAGE_SIZE ? (
          <div className={styles.pagination}>
            <button
              type="button"
              className={styles.pageBtn}
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              ← 이전
            </button>
            <span className={styles.pageInfo}>
              {currentPage} / {totalPages}
            </span>
            <button
              type="button"
              className={styles.pageBtn}
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              다음 →
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
