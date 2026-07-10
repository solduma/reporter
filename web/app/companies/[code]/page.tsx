"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import AnalysisPanel from "@/components/AnalysisPanel";
import { MA_DEFS } from "@/components/CandleChart";
import type { ChartRange } from "@/components/CandleChart";
import CompanyTimeline from "@/components/CompanyTimeline";
import DateRangeSlider from "@/components/DateRangeSlider";
import GrowthMetrics from "@/components/GrowthMetrics";
import PeersTable from "@/components/PeersTable";
import SectorCharts from "@/components/SectorCharts";
import {
  fetchCandles,
  fetchCompanyAnalysis,
  fetchCompanySummary,
  fetchFinancials,
  fetchPeers,
} from "@/lib/api";
import { dateToTs, monthsAgoIso } from "@/lib/chartTime";
import { addQuickPick } from "@/lib/quickPicks";
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
const FinancialsLineChart = dynamic(() => import("@/components/FinancialsLineChart"), {
  ssr: false,
  loading: () => <div className={styles.sectionStatus}>차트 불러오는 중…</div>,
});
const MultipleBandChart = dynamic(() => import("@/components/MultipleBandChart"), {
  ssr: false,
  loading: () => <div className={styles.sectionStatus}>차트 불러오는 중…</div>,
});

interface ViewDef {
  id: Timeframe;
  label: string;
}

// 분(30분봉)/일/주 버튼탭. 지수·섹터(/api/chart)는 30분봉이 없어 '분' 선택 시 일봉으로 폴백한다.
const VIEWS: ViewDef[] = [
  { id: "30m", label: "분" },
  { id: "day", label: "일" },
  { id: "week", label: "주" },
];

// 비교 차트(지수·섹터)용 timeframe: 30m 은 없으므로 day 로 폴백.
function compareTf(tf: Timeframe): ChartTimeframe {
  return tf === "30m" ? "day" : tf;
}

// 재무 기간('2026.03') → 분기말 'YYYY-MM-DD'. 밸류 밴드 슬라이더 날짜축용
// (MultipleBandChart 의 periodToDate 와 동일 규칙).
const QUARTER_END: Record<string, string> = {
  "03": "-03-31",
  "06": "-06-30",
  "09": "-09-30",
  "12": "-12-31",
};
function periodToIso(period: string): string | null {
  const m = period.match(/(\d{4})\.(\d{2})/);
  const tail = m ? QUARTER_END[m[2]] : undefined;
  return m && tail ? `${m[1]}${tail}` : null;
}

// 각 섹션이 독립적으로 로딩/실패하도록 상태를 분리해 관리한다.
type SectionState<T> = { status: "loading" | "ready" | "error"; data: T; message?: string };

export default function CompanyDetailPage({ params }: { params: { code: string } }) {
  const { code } = params;

  const [summary, setSummary] = useState<CompanySummary | null>(null);
  // 비교 차트 전체가 공유하는 봉 종류(분/일/주)와 표시 날짜 범위(date-range).
  const [timeframe, setTimeframe] = useState<Timeframe>("day");
  const [candlesByTf, setCandlesByTf] = useState<Partial<Record<Timeframe, CandlePoint[]>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // date-range: 시작·종료일(ISO). null 이면 데이터 로드 후 최근 3개월로 초기화.
  const [dateRange, setDateRange] = useState<{ from: string; to: string } | null>(null);
  // 밸류에이션 밴드 전용 기간(탑다운 슬라이더와 독립). null 이면 재무 로드 후 전체 구간으로 초기화.
  const [valuationRange, setValuationRange] = useState<{ from: string; to: string } | null>(null);

  const [analysis, setAnalysis] = useState<SectionState<CompanyAnalysis | null>>({
    status: "loading",
    data: null,
  });
  const krSector = analysis.data?.topdown?.kr_sector ?? null;
  const market = analysis.data?.market ?? null;

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
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    const MAX_POLLS = 10;
    async function load(poll = false, attempt = 0) {
      if (!poll) {
        setAnalysis({ status: "loading", data: null });
      }
      try {
        const res = await fetchCompanyAnalysis(code);
        if (!active) {
          return;
        }
        setAnalysis({ status: "ready", data: res });
        if (res.comment_pending && attempt < MAX_POLLS) {
          pollTimer = setTimeout(() => void load(true, attempt + 1), 3000);
        }
      } catch (e) {
        if (active && !poll) {
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
      if (pollTimer) {
        clearTimeout(pollTimer);
      }
      active = false;
    };
  }, [code]);

  // 종목 봉(분/일/주). 이 종목의 일자 축이 date-range 슬라이더·전체 비교차트의 기준이 된다.
  useEffect(() => {
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

  const stockCandles = useMemo(() => candlesByTf[timeframe] ?? [], [candlesByTf, timeframe]);

  // 슬라이더가 다룰 날짜 축(오름차순 'YYYY-MM-DD'). 종목 봉에서 뽑는다.
  const dateAxis = useMemo(
    () => stockCandles.map((c) => c.t.slice(0, 10)),
    [stockCandles],
  );

  // 종목 봉이 로드되면 date-range 기본값을 최근 3개월로 초기화(범위 밖이면 클램프).
  useEffect(() => {
    if (dateAxis.length === 0) {
      return;
    }
    const first = dateAxis[0];
    const last = dateAxis[dateAxis.length - 1];
    const threeMoAgo = monthsAgoIso(3, new Date(`${last}T00:00:00Z`));
    const from = threeMoAgo < first ? first : threeMoAgo;
    setDateRange((prev) => prev ?? { from, to: last });
  }, [dateAxis]);

  // 모든 차트가 공유할 표시 구간(lightweight-charts Time).
  const chartRange: ChartRange | null = useMemo(
    () => (dateRange ? { from: dateToTs(dateRange.from), to: dateToTs(dateRange.to) } : null),
    [dateRange],
  );

  // 밸류에이션 밴드 슬라이더의 날짜축 — 재무 분기말 'YYYY-MM-DD' 오름차순(중복 제거).
  const valuationAxis = useMemo(() => {
    const isos = financials.data
      .map((f) => periodToIso(f.period))
      .filter((v): v is string => v !== null);
    return Array.from(new Set(isos)).sort();
  }, [financials.data]);

  // 재무 로드되면 밸류 밴드 기간을 전체 구간으로 초기화(한 번만).
  useEffect(() => {
    if (valuationAxis.length > 0) {
      setValuationRange(
        (prev) => prev ?? { from: valuationAxis[0], to: valuationAxis[valuationAxis.length - 1] },
      );
    }
  }, [valuationAxis]);

  const valuationChartRange: ChartRange | null = useMemo(
    () =>
      valuationRange
        ? { from: dateToTs(valuationRange.from), to: dateToTs(valuationRange.to) }
        : null,
    [valuationRange],
  );

  const displayName = summary?.stock_name ?? "이름 미상";

  // 조회한 종목을 '자주 찾는 종목'(localStorage)에 자동 추가. 이름이 확인된 뒤에만 등록해
  // '이름 미상'이 목록에 남지 않게 한다.
  useEffect(() => {
    if (summary?.stock_name) {
      addQuickPick({ code: summary.stock_code ?? code, name: summary.stock_name });
    }
  }, [summary, code]);

  const stockChart = useMemo(() => {
    if (loading && stockCandles.length === 0) {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (stockCandles.length === 0) {
      return <div className={styles.chartStatus}>차트 데이터가 없습니다</div>;
    }
    return (
      <CandleChart data={stockCandles} timeframe={timeframe} range={chartRange} showControls={false} />
    );
  }, [loading, stockCandles, timeframe, chartRange]);

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

      {/* 탑다운 비교 차트: 지수 → 섹터 → 종목 → 재무. 공용 컨트롤바(분/일/주·기간·MA). */}
      <section className={styles.chartCard}>
        <div className={styles.growthHead}>
          <div>
            <h2 className={styles.sectionTitle}>탑다운 비교 차트</h2>
            <p className={styles.compareSub}>지수 · 섹터 · 종목 · 재무를 같은 기간으로 함께 본다</p>
          </div>
        </div>

        <div className={styles.controlBar}>
          <div className={styles.tabs} role="tablist" aria-label="봉 종류">
            {VIEWS.map((v) => {
              const on = v.id === timeframe;
              return (
                <button
                  key={v.id}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  className={on ? `${styles.tab} ${styles.active}` : styles.tab}
                  onClick={() => setTimeframe(v.id)}
                >
                  {v.label}
                </button>
              );
            })}
          </div>
          {dateRange && dateAxis.length > 1 ? (
            <DateRangeSlider
              dates={dateAxis}
              from={dateRange.from}
              to={dateRange.to}
              onChange={(from, to) => setDateRange({ from, to })}
            />
          ) : null}
          <div className={styles.maLegend} aria-label="이동평균선">
            {MA_DEFS.map((m) => (
              <span key={m.period} className={styles.maItem}>
                <span className={styles.maDot} style={{ background: m.color }} />
                MA{m.period}
              </span>
            ))}
          </div>
        </div>

        {/* 지수 → 섹터 (2열 국장|미장) */}
        {krSector ? (
          <SectorCharts
            industry={krSector}
            timeframe={compareTf(timeframe)}
            market={market ?? undefined}
            dateRange={dateRange}
          />
        ) : (
          <p className={styles.sectionStatus}>
            이 종목의 섹터를 특정할 수 없어 지수·섹터 차트를 생략합니다.
          </p>
        )}

        {/* 종목 */}
        <div className={styles.compareStock}>
          <h3 className={styles.subHead}>{displayName} (종목)</h3>
          {stockChart}
        </div>

        {/* 재무 라인(종목 차트 아래, 같은 시간축·기간) */}
        <div className={styles.compareStock}>
          <h3 className={styles.subHead}>재무 지표</h3>
          {financials.status === "ready" && financials.data.length > 0 ? (
            <FinancialsLineChart data={financials.data} range={chartRange} />
          ) : financials.status === "loading" ? (
            <div className={styles.sectionStatus}>불러오는 중…</div>
          ) : (
            <div className={styles.sectionStatus}>재무 데이터가 없습니다</div>
          )}
        </div>
      </section>

      {/* PER · PBR · PSR 분위수 밴드 (자체 date-range 슬라이더로 3개 차트 동시 조작) */}
      <section className={styles.chartCard}>
        <h2 className={styles.sectionTitle}>밸류에이션 밴드 (PER · PBR · PSR)</h2>
        {financials.status === "ready" && financials.data.length > 0 ? (
          <>
            {valuationRange && valuationAxis.length > 1 ? (
              <div className={styles.controlBar}>
                <DateRangeSlider
                  dates={valuationAxis}
                  from={valuationRange.from}
                  to={valuationRange.to}
                  onChange={(from, to) => setValuationRange({ from, to })}
                />
              </div>
            ) : null}
            <MultipleBandChart data={financials.data} range={valuationChartRange} />
          </>
        ) : (
          <div className={styles.sectionStatus}>재무 데이터가 없습니다</div>
        )}
      </section>

      <section className={styles.chartCard}>
        <h2 className={styles.sectionTitle}>동일업종비교</h2>
        {peersArea}
      </section>
    </div>
  );
}
