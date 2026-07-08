"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import PeersTable from "@/components/PeersTable";
import { fetchCandles, fetchCompanySummary, fetchFinancials, fetchPeers } from "@/lib/api";
import type {
  CandlePoint,
  CompanySummary,
  FinancialPeriod,
  Peer,
  Timeframe,
} from "@/lib/types";

import styles from "./page.module.css";

// lightweight-charts는 캔버스 기반 브라우저 전용이라 SSR을 끈다.
const CandleChart = dynamic(() => import("@/components/CandleChart"), {
  ssr: false,
  loading: () => <div className={styles.chartStatus}>차트 불러오는 중…</div>,
});

// Recharts는 브라우저 전용(ResponsiveContainer가 DOM 크기에 의존)이라 SSR을 끈다.
const FinancialsChart = dynamic(() => import("@/components/FinancialsChart"), {
  ssr: false,
  loading: () => <div className={styles.sectionStatus}>차트 불러오는 중…</div>,
});

// 최근 3개월 ≈ 63 거래일. 일봉 전체를 잘라 재사용한다.
const THREE_MONTH_SESSIONS = 63;

type ViewId = "30m" | "3m" | "1y" | "3y";

interface ViewDef {
  id: ViewId;
  label: string;
  timeframe: Timeframe;
}

const VIEWS: ViewDef[] = [
  { id: "30m", label: "2주 30분봉", timeframe: "30m" },
  { id: "3m", label: "3개월", timeframe: "day" },
  { id: "1y", label: "1년", timeframe: "day" },
  { id: "3y", label: "3년 월봉", timeframe: "month" },
];

// 각 섹션이 독립적으로 로딩/실패하도록 상태를 분리해 관리한다.
type SectionState<T> = { status: "loading" | "ready" | "error"; data: T; message?: string };

export default function CompanyDetailPage({ params }: { params: { code: string } }) {
  const { code } = params;

  const [summary, setSummary] = useState<CompanySummary | null>(null);
  const [view, setView] = useState<ViewId>("3m");
  const [candlesByTf, setCandlesByTf] = useState<Partial<Record<Timeframe, CandlePoint[]>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [financials, setFinancials] = useState<SectionState<FinancialPeriod[]>>({
    status: "loading",
    data: [],
  });
  const [peers, setPeers] = useState<SectionState<Peer[]>>({ status: "loading", data: [] });

  const timeframe = VIEWS.find((v) => v.id === view)?.timeframe ?? "day";

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const res = await fetchCompanySummary(code);
        if (active) {
          setSummary(res);
        }
      } catch {
        // 요약 실패는 헤더에만 영향 — 차트 흐름을 막지 않도록 코드만 표시한다.
        if (active) {
          setSummary(null);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  useEffect(() => {
    // 이미 받은 타임프레임은 재요청하지 않는다(일봉은 3개월·1년이 공유).
    if (candlesByTf[timeframe]) {
      setError(null);
      return;
    }
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchCandles(code, timeframe);
        if (active) {
          setCandlesByTf((prev) => ({ ...prev, [timeframe]: res }));
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "차트 데이터를 불러오지 못했습니다");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code, timeframe, candlesByTf]);

  useEffect(() => {
    let active = true;
    async function load() {
      setFinancials({ status: "loading", data: [] });
      try {
        const res = await fetchFinancials(code);
        if (active) {
          setFinancials({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setFinancials({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "재무 데이터를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  useEffect(() => {
    let active = true;
    async function load() {
      setPeers({ status: "loading", data: [] });
      try {
        const res = await fetchPeers(code);
        if (active) {
          setPeers({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setPeers({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "동일업종 데이터를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  const chartData = useMemo(() => {
    const raw = candlesByTf[timeframe] ?? [];
    if (view === "3m") {
      return raw.slice(-THREE_MONTH_SESSIONS);
    }
    return raw;
  }, [candlesByTf, timeframe, view]);

  const chartArea = useMemo(() => {
    if (loading && !candlesByTf[timeframe]) {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (chartData.length === 0) {
      return <div className={styles.chartStatus}>차트 데이터가 없습니다</div>;
    }
    return <CandleChart data={chartData} timeframe={timeframe} />;
  }, [loading, candlesByTf, timeframe, chartData]);

  const financialsArea = useMemo(() => {
    if (financials.status === "loading") {
      return <div className={styles.sectionStatus}>불러오는 중…</div>;
    }
    if (financials.status === "error") {
      return <p className={styles.error}>API 연결 실패: {financials.message}</p>;
    }
    if (financials.data.length === 0) {
      return <div className={styles.sectionStatus}>재무 데이터가 없습니다</div>;
    }
    return <FinancialsChart data={financials.data} />;
  }, [financials]);

  const peersArea = useMemo(() => {
    if (peers.status === "loading") {
      return <div className={styles.sectionStatus}>불러오는 중…</div>;
    }
    if (peers.status === "error") {
      return <p className={styles.error}>API 연결 실패: {peers.message}</p>;
    }
    if (peers.data.length === 0) {
      return <div className={styles.sectionStatus}>동일업종 데이터가 없습니다</div>;
    }
    return <PeersTable peers={peers.data} baseCode={code} />;
  }, [peers, code]);

  const displayName = summary?.stock_name ?? "이름 미상";

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>{displayName}</h1>
        <span className={styles.code}>{summary?.stock_code ?? code}</span>
      </header>

      {error ? <p className={styles.error}>API 연결 실패: {error}</p> : null}

      <section className={styles.chartCard}>
        <div className={styles.tabs} role="tablist" aria-label="기간 선택">
          {VIEWS.map((v) => {
            const active = v.id === view;
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={active ? `${styles.tab} ${styles.active}` : styles.tab}
                onClick={() => setView(v.id)}
              >
                {v.label}
              </button>
            );
          })}
        </div>
        {chartArea}
      </section>

      <section className={styles.chartCard}>
        <h2 className={styles.sectionTitle}>재무제표</h2>
        {financialsArea}
      </section>

      <section className={styles.chartCard}>
        <h2 className={styles.sectionTitle}>동일업종비교</h2>
        {peersArea}
      </section>
    </div>
  );
}
