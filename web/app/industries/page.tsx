"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import IndustrySelector from "@/components/IndustrySelector";
import ReportRefDrawer from "@/components/ReportRefDrawer";
import {
  fetchIndustries,
  fetchIndustrySentiment,
  fetchTrade,
  fetchTradePresets,
} from "@/lib/api";
import type { Industry, SentimentPoint, TradePoint, TradePresets } from "@/lib/types";

import styles from "./page.module.css";

// Recharts는 브라우저 전용(ResponsiveContainer가 DOM 크기에 의존)이라 SSR을 끈다.
const SentimentChart = dynamic(() => import("@/components/SentimentChart"), {
  ssr: false,
  loading: () => <div className={styles.chartStatus}>차트 불러오는 중…</div>,
});

const TradeChart = dynamic(() => import("@/components/TradeChart"), {
  ssr: false,
  loading: () => <div className={styles.chartStatus}>차트 불러오는 중…</div>,
});

// 관세청 API는 조회 범위가 1년 이내여야 하므로 최근 12개월(현재월 포함)로 계산한다.
function tradeRange(now: Date): { start: string; end: string } {
  const yyyymm = (d: Date) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`;
  const start = new Date(now.getFullYear(), now.getMonth() - 11, 1);
  return { start: yyyymm(start), end: yyyymm(now) };
}

function formatRange(yyyymm: string): string {
  return `${yyyymm.slice(0, 4)}.${yyyymm.slice(4, 6)}`;
}

export default function IndustriesPage() {
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [points, setPoints] = useState<SentimentPoint[]>([]);
  const [selectedPoint, setSelectedPoint] = useState<SentimentPoint | null>(null);
  const [industriesLoading, setIndustriesLoading] = useState(true);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [presets, setPresets] = useState<TradePresets>({});
  const [selectedHs, setSelectedHs] = useState<string | null>(null);
  const [tradePoints, setTradePoints] = useState<TradePoint[]>([]);
  const [tradeLoading, setTradeLoading] = useState(true);
  const [tradeError, setTradeError] = useState<string | null>(null);

  // 현재 날짜 기준 최근 12개월. 마운트 시 한 번만 고정한다.
  const range = useMemo(() => tradeRange(new Date()), []);

  useEffect(() => {
    let active = true;
    async function load() {
      setIndustriesLoading(true);
      setError(null);
      try {
        const res = await fetchIndustries();
        if (!active) {
          return;
        }
        setIndustries(res);
        setSelected(res[0]?.industry ?? null);
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "산업 목록을 불러오지 못했습니다");
        }
      } finally {
        if (active) {
          setIndustriesLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setPoints([]);
      return;
    }
    let active = true;
    async function load(name: string) {
      setSeriesLoading(true);
      setError(null);
      setSelectedPoint(null);
      try {
        const res = await fetchIndustrySentiment(name);
        if (!active) {
          return;
        }
        setPoints(res);
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "센티먼트 데이터를 불러오지 못했습니다");
          setPoints([]);
        }
      } finally {
        if (active) {
          setSeriesLoading(false);
        }
      }
    }
    void load(selected);
    return () => {
      active = false;
    };
  }, [selected]);

  // 무역통계 품목 목록. 센티먼트 섹션과 독립적으로 로드된다.
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const res = await fetchTradePresets();
        if (!active) {
          return;
        }
        setPresets(res);
        setSelectedHs(Object.keys(res)[0] ?? null);
      } catch (e) {
        if (active) {
          setTradeError(e instanceof Error ? e.message : "무역통계 품목을 불러오지 못했습니다");
          setTradeLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedHs) {
      return;
    }
    let active = true;
    async function load(hs: string) {
      setTradeLoading(true);
      setTradeError(null);
      try {
        const res = await fetchTrade(hs, range.start, range.end);
        if (!active) {
          return;
        }
        setTradePoints(res);
      } catch (e) {
        if (active) {
          setTradeError(e instanceof Error ? e.message : "무역통계 데이터를 불러오지 못했습니다");
          setTradePoints([]);
        }
      } finally {
        if (active) {
          setTradeLoading(false);
        }
      }
    }
    void load(selectedHs);
    return () => {
      active = false;
    };
  }, [selectedHs, range]);

  const chartArea = useMemo(() => {
    if (seriesLoading) {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (points.length === 0) {
      return <div className={styles.chartStatus}>표시할 센티먼트 데이터가 없습니다.</div>;
    }
    return (
      <SentimentChart
        data={points}
        selectedDate={selectedPoint?.date ?? null}
        onSelectPoint={setSelectedPoint}
      />
    );
  }, [seriesLoading, points, selectedPoint]);

  const tradeChartArea = useMemo(() => {
    if (tradeError) {
      return <div className={styles.chartStatus}>{tradeError}</div>;
    }
    if (tradeLoading) {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (tradePoints.length === 0) {
      return <div className={styles.chartStatus}>무역통계 데이터가 없습니다</div>;
    }
    return <TradeChart data={tradePoints} />;
  }, [tradeError, tradeLoading, tradePoints]);

  const presetEntries = Object.entries(presets);

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>산업 흐름</h1>
        <p className={styles.subtitle}>산업별 투자의견(BUY +1 · HOLD 0 · SELL −1) 추이</p>
      </header>

      {error ? <p className={styles.error}>API 연결 실패: {error}</p> : null}

      {industriesLoading ? (
        <p className={styles.loading}>산업 목록 불러오는 중…</p>
      ) : industries.length === 0 ? (
        <p className={styles.loading}>산업 데이터가 없습니다</p>
      ) : (
        <>
          <IndustrySelector industries={industries} selected={selected} onSelect={setSelected} />

          <div className={styles.layout}>
            <section className={styles.chartCard}>
              <div className={styles.chartHead}>
                <h2 className={styles.chartTitle}>{selected}</h2>
                <div className={styles.legend}>
                  <span className={styles.legendItem}>
                    <span className={`${styles.dot} ${styles.buyDot}`} />긍정(BUY)
                  </span>
                  <span className={styles.legendItem}>
                    <span className={`${styles.dot} ${styles.sellDot}`} />부정(SELL)
                  </span>
                </div>
              </div>
              {chartArea}
            </section>

            <ReportRefDrawer point={selectedPoint} onClose={() => setSelectedPoint(null)} />
          </div>
        </>
      )}

      <section className={styles.tradeSection}>
        <div className={styles.tradeHead}>
          <h2 className={styles.title}>무역통계</h2>
          <p className={styles.subtitle}>
            관세청 수출입 무역통계 — 월별 수출 · 수입 · 무역수지 (USD)
            <span className={styles.tradeRange}>
              {" "}
              · {formatRange(range.start)} ~ {formatRange(range.end)}
            </span>
          </p>
        </div>

        {presetEntries.length > 0 ? (
          <div className={styles.tradeChips} role="tablist" aria-label="품목(HS) 선택">
            {presetEntries.map(([hs, name]) => {
              const active = hs === selectedHs;
              return (
                <button
                  key={hs}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={
                    active ? `${styles.tradeChip} ${styles.tradeChipActive}` : styles.tradeChip
                  }
                  onClick={() => setSelectedHs(hs)}
                >
                  {name}
                </button>
              );
            })}
          </div>
        ) : null}

        <section className={styles.chartCard}>{tradeChartArea}</section>
      </section>
    </div>
  );
}
