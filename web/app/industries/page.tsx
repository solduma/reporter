"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import IndustrySelector from "@/components/IndustrySelector";
import ReportRefDrawer from "@/components/ReportRefDrawer";
import { fetchIndustries, fetchIndustrySentiment } from "@/lib/api";
import type { Industry, SentimentPoint } from "@/lib/types";

import styles from "./page.module.css";

// Recharts는 브라우저 전용(ResponsiveContainer가 DOM 크기에 의존)이라 SSR을 끈다.
const SentimentChart = dynamic(() => import("@/components/SentimentChart"), {
  ssr: false,
  loading: () => <div className={styles.chartStatus}>차트 불러오는 중…</div>,
});

export default function IndustriesPage() {
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [points, setPoints] = useState<SentimentPoint[]>([]);
  const [selectedPoint, setSelectedPoint] = useState<SentimentPoint | null>(null);
  const [industriesLoading, setIndustriesLoading] = useState(true);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    </div>
  );
}
