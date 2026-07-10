"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import AnalysisPanel from "@/components/AnalysisPanel";
import CompanyTimeline from "@/components/CompanyTimeline";
import GrowthMetrics from "@/components/GrowthMetrics";
import PeersTable from "@/components/PeersTable";
import SectorCharts from "@/components/SectorCharts";
import SymbolChartCard from "@/components/SymbolChartCard";
import TimeframeSlider from "@/components/TimeframeSlider";
import { fetchCandles, fetchCompanyAnalysis, fetchCompanySummary, fetchFinancials, fetchPeers } from "@/lib/api";
import type {
  CandlePoint,
  ChartTimeframe,
  CompanyAnalysis,
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

interface ViewDef {
  id: Timeframe;
  label: string;
}

// 분(2주 30분봉) / 일(2년) / 주(10년). id 가 곧 timeframe.
const VIEWS: ViewDef[] = [
  { id: "30m", label: "분" },
  { id: "day", label: "일" },
  { id: "week", label: "주" },
];

// 각 섹션이 독립적으로 로딩/실패하도록 상태를 분리해 관리한다.
type SectionState<T> = { status: "loading" | "ready" | "error"; data: T; message?: string };

export default function CompanyDetailPage({ params }: { params: { code: string } }) {
  const { code } = params;

  const [summary, setSummary] = useState<CompanySummary | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("day");
  const [candlesByTf, setCandlesByTf] = useState<Partial<Record<Timeframe, CandlePoint[]>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 분석 결과는 페이지가 한 번만 조회해 AnalysisPanel 과 탑다운 비교 차트가 공유한다
  // (/analysis 는 매 호출 LLM 코멘트를 생성하므로 중복 조회를 피한다).
  const [analysis, setAnalysis] = useState<SectionState<CompanyAnalysis | null>>({
    status: "loading",
    data: null,
  });
  const krSector = analysis.data?.topdown?.kr_sector ?? null;
  // 비교 차트 3종을 함께 조정하는 공용 기간(일/주/월).
  const [compareTf, setCompareTf] = useState<ChartTimeframe>("day");

  const [financials, setFinancials] = useState<SectionState<FinancialPeriod[]>>({
    status: "loading",
    data: [],
  });
  const [peers, setPeers] = useState<SectionState<Peer[]>>({ status: "loading", data: [] });

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
    let active = true;
    async function load() {
      setAnalysis({ status: "loading", data: null });
      try {
        const res = await fetchCompanyAnalysis(code);
        if (active) {
          setAnalysis({ status: "ready", data: res });
        }
      } catch (e) {
        // 분석 실패 시 비교 차트(지수·섹터)만 생략한다 — 종목 차트는 별도로 뜬다.
        if (active) {
          setAnalysis({
            status: "error",
            data: null,
            message: e instanceof Error ? e.message : "분석을 불러오지 못했습니다",
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

  const chartArea = useMemo(() => {
    const chartData = candlesByTf[timeframe] ?? [];
    if (loading && !candlesByTf[timeframe]) {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (chartData.length === 0) {
      return <div className={styles.chartStatus}>차트 데이터가 없습니다</div>;
    }
    return <CandleChart data={chartData} timeframe={timeframe} />;
  }, [loading, candlesByTf, timeframe]);

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
        <div className={styles.growthHead}>
          <h2 className={styles.sectionTitle}>테크노펀더멘탈 분석</h2>
          <span className={styles.growthTag}>성장·기술·탑다운</span>
        </div>
        <AnalysisPanel
          code={code}
          analysis={analysis.data}
          status={analysis.status}
          message={analysis.message}
        />
      </section>

      <section className={styles.chartCard}>
        <div className={styles.growthHead}>
          <h2 className={styles.sectionTitle}>성장 지표</h2>
          <span className={styles.growthTag}>성장주 스냅샷</span>
        </div>
        <GrowthMetrics code={code} />
      </section>

      <section className={styles.chartCard}>
        <h2 className={styles.sectionTitle}>타임라인</h2>
        <CompanyTimeline code={code} />
      </section>

      <section className={styles.chartCard}>
        <div className={styles.tabs} role="tablist" aria-label="기간 선택">
          {VIEWS.map((v) => {
            const active = v.id === timeframe;
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={active ? `${styles.tab} ${styles.active}` : styles.tab}
                onClick={() => setTimeframe(v.id)}
              >
                {v.label}
              </button>
            );
          })}
        </div>
        {chartArea}
      </section>

      <section className={styles.chartCard}>
        <div className={styles.growthHead}>
          <div>
            <h2 className={styles.sectionTitle}>탑다운 비교 차트</h2>
            <p className={styles.compareSub}>지수 · 섹터 · 종목을 같은 기간으로 함께 본다</p>
          </div>
          <TimeframeSlider value={compareTf} onChange={setCompareTf} label="기간" />
        </div>
        <div className={styles.compareStock}>
          <SymbolChartCard
            symbol={summary?.stock_code ?? code}
            market="KR"
            timeframe={compareTf}
            label={`${displayName} (종목)`}
          />
        </div>
        {krSector ? (
          <SectorCharts industry={krSector} timeframe={compareTf} />
        ) : (
          <p className={styles.sectionStatus}>
            이 종목의 섹터를 특정할 수 없어 지수·섹터 차트를 생략합니다.
          </p>
        )}
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
