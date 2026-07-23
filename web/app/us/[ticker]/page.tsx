"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import {
  fetchChart,
  fetchUsDisclosures,
  fetchUsFinancials,
  fetchUsFinancialsOntology,
  fetchUsQuote,
} from "@/lib/api";
import type {
  CandlePoint,
  ChartTimeframe,
  UsDisclosure,
  UsFinancial,
  UsFinancialOntologyItem,
  UsQuote,
} from "@/lib/types";

import styles from "./page.module.css";

// lightweight-charts 는 브라우저 전용이라 SSR 을 끈다(company 페이지와 동일).
const CandleChart = dynamic(() => import("@/components/CandleChart"), {
  ssr: false,
  loading: () => <div className={styles.chartStatus}>차트 불러오는 중…</div>,
});

const VIEWS: { id: ChartTimeframe; label: string }[] = [
  { id: "day", label: "일" },
  { id: "week", label: "주" },
  { id: "month", label: "월" },
];

// USD 대금액을 $X.XB / $X.XT 로 축약.
function usdShort(v: number | null): string {
  if (v === null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

function num(v: number | null, suffix = ""): string {
  return v === null ? "—" : `${v}${suffix}`;
}

function fmtOntologyValue(item: UsFinancialOntologyItem): string {
  const v = item.value;
  if (v === null) return "—";
  switch (item.key) {
    case "ttm_revenue":
    case "ttm_net_income":
    case "ttm_operating_income":
    case "equity":
      return usdShort(v);
    case "ttm_eps":
      return `$${v}`;
    case "roe":
      return `${v}%`;
    default:
      return num(v);
  }
}

export default function UsCompanyPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [quote, setQuote] = useState<UsQuote | null>(null);
  const [fin, setFin] = useState<UsFinancial | null>(null);
  const [candles, setCandles] = useState<CandlePoint[]>([]);
  const [tf, setTf] = useState<ChartTimeframe>("day");
  const [disclosures, setDisclosures] = useState<UsDisclosure[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ontologyItems, setOntologyItems] = useState<UsFinancialOntologyItem[]>([]);

  // 시세 + 재무(각자 독립 로드). 재무는 SEC 첫 계산이 느릴 수 있어 시세를 먼저 보여준다.
  useEffect(() => {
    let active = true;
    setError(null);
    void fetchUsQuote(ticker)
      .then((q) => active && setQuote(q))
      .catch(() => active && setError("US 종목 시세를 찾지 못했습니다"));
    void fetchUsFinancials(ticker)
      .then((f) => active && setFin(f))
      .catch(() => {
        /* 재무 없음(SEC 미등록)은 치명적 아님 — 차트·시세만 표시 */
      });
    void fetchUsFinancialsOntology(ticker)
      .then((o) => active && setOntologyItems(o.items))
      .catch(() => {
        /* 온톨로지 매핑 실패 시 기존 재무 카드로만 표시 */
      });
    void fetchUsDisclosures(ticker)
      .then((d) => active && setDisclosures(d))
      .catch(() => {
        /* 공시 없음(배치 미수집)은 무해 */
      });
    return () => {
      active = false;
    };
  }, [ticker]);

  // 차트는 quote 의 naver_symbol 로 조회(.O/.N 접미사 자동 해석된 심볼).
  useEffect(() => {
    if (!quote?.naver_symbol) return;
    let active = true;
    void fetchChart(quote.naver_symbol, "US", tf)
      .then((c) => active && setCandles(c))
      .catch(() => active && setCandles([]));
    return () => {
      active = false;
    };
  }, [quote?.naver_symbol, tf]);

  const metrics = useMemo(
    () => [
      { label: "PER", value: num(fin?.per ?? null) },
      { label: "PBR", value: num(fin?.pbr ?? null) },
      { label: "PSR", value: num(fin?.psr ?? null) },
      { label: "ROE", value: num(fin?.roe ?? null, "%") },
      { label: "시가총액", value: usdShort(fin?.market_cap ?? null) },
      { label: "TTM 매출", value: usdShort(fin?.ttm_revenue ?? null) },
      { label: "TTM 순이익", value: usdShort(fin?.ttm_net_income ?? null) },
      { label: "TTM EPS", value: fin?.ttm_eps !== null && fin?.ttm_eps !== undefined ? `$${fin.ttm_eps}` : "—" },
    ],
    [fin],
  );

  const rising = quote?.rising;
  const priceClass = rising === true ? styles.up : rising === false ? styles.down : "";

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>{quote?.name ?? ticker}</h1>
        <span className={styles.ticker}>{ticker}</span>
        <span className={styles.usTag}>US · SEC EDGAR</span>
        {quote?.close !== null && quote?.close !== undefined ? (
          <span className={`${styles.price} ${priceClass}`}>
            ${quote.close.toLocaleString("en-US")}
            {quote.change_ratio ? <span className={styles.ratio}> {quote.change_ratio}%</span> : null}
          </span>
        ) : null}
      </header>

      {error ? <p className={styles.error}>{error}</p> : null}

      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.sectionTitle}>밸류에이션 · 재무 (TTM)</h2>
          {!fin ? <span className={styles.status}>SEC 재무 계산 중…</span> : null}
        </div>
        <div className={styles.metrics}>
          {metrics.map((m) => (
            <div key={m.label} className={styles.metric}>
              <span className={styles.metricLabel}>{m.label}</span>
              <span className={styles.metricValue}>{m.value}</span>
            </div>
          ))}
        </div>
      </section>

      {ontologyItems.length > 0 ? (
        <section className={styles.card}>
          <div className={styles.cardHead}>
            <h2 className={styles.sectionTitle}>온톨로지 정규화 지표</h2>
          </div>
          <div className={styles.ontologyTable}>
            {ontologyItems.map((item) => (
              <div key={item.key} className={styles.ontologyRow}>
                <span className={styles.ontologyKey}>{item.key}</span>
                <span className={styles.ontologyLabel}>{item.label}</span>
                <span className={styles.ontologyValue}>{fmtOntologyValue(item)}</span>
                {item.description ? (
                  <span className={styles.ontologyDescription}>{item.description}</span>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.sectionTitle}>주가 차트</h2>
          <div className={styles.tabs} role="tablist" aria-label="봉 종류">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={v.id === tf}
                className={v.id === tf ? `${styles.tab} ${styles.active}` : styles.tab}
                onClick={() => setTf(v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>
        </div>
        {candles.length > 0 ? (
          <CandleChart data={candles} timeframe={tf} showControls={false} />
        ) : (
          <div className={styles.chartStatus}>차트 데이터가 없습니다</div>
        )}
      </section>

      {disclosures.length > 0 ? (
        <section className={styles.card}>
          <div className={styles.cardHead}>
            <h2 className={styles.sectionTitle}>최근 공시 (SEC 8-K)</h2>
          </div>
          <ul className={styles.filings}>
            {disclosures.map((d) => (
              <li key={d.accession} className={styles.filing}>
                <span className={styles.filingDate}>{d.filing_date}</span>
                <a href={d.primary_doc_url} target="_blank" rel="noreferrer" className={styles.filingTitle}>
                  {d.title ?? d.form_type}
                </a>
                <span className={styles.filingForm}>{d.form_type}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
