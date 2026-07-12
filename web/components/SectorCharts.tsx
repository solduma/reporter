"use client";

import { useEffect, useState } from "react";

import SymbolChartCard from "@/components/SymbolChartCard";
import { fetchSectorChartMeta } from "@/lib/api";
import type { ChartRef, ChartTimeframe, SectorChartMeta } from "@/lib/types";

import styles from "./SectorCharts.module.css";

type State = {
  status: "loading" | "ready" | "error";
  meta: SectorChartMeta | null;
  message?: string;
};

interface Props {
  industry: string;
  timeframe: ChartTimeframe;
  market?: string; // 종목 시장(KOSPI|KOSDAQ) — 표시할 국내 지수 선택용
  dateRange?: { from: string; to: string } | null;
  onRangeChange?: (from: string, to: string) => void; // 차트 조작 시 공유 구간 갱신
}

// 지수·섹터를 2열(왼쪽=국장, 오른쪽=미장)로 배치. 지수는 해당 종목 지수 1개 + 나스닥(QQQ)만.
export default function SectorCharts({
  industry,
  timeframe,
  market,
  dateRange = null,
  onRangeChange,
}: Props) {
  const [state, setState] = useState<State>({ status: "loading", meta: null });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", meta: null });
      try {
        const meta = await fetchSectorChartMeta(industry);
        if (active) {
          setState({ status: "ready", meta });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            meta: null,
            message: e instanceof Error ? e.message : "차트 구성을 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [industry]);

  if (state.status === "loading") {
    return <div className={styles.status}>차트 구성 불러오는 중…</div>;
  }
  if (state.status === "error") {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }

  const meta = state.meta;
  if (!meta) {
    return null;
  }

  // 국내 지수: 종목 시장에 맞춰 코스피/코스닥 중 하나. 미국: 나스닥100(QQQ).
  const krIndexLabel = market === "KOSDAQ" ? "코스닥" : "코스피";
  const krIndex =
    meta.indices.find((r) => r.market === "KR" && r.label === krIndexLabel) ??
    meta.indices.find((r) => r.market === "KR") ??
    null;
  const nasdaq =
    meta.indices.find((r) => r.market === "US" && r.label.includes("나스닥")) ??
    meta.indices.find((r) => r.market === "US") ??
    null;

  const card = (ref: ChartRef, key: string) => (
    <SymbolChartCard
      key={key}
      symbol={ref.symbol}
      market={ref.market}
      timeframe={timeframe}
      label={ref.label}
      dateRange={dateRange}
      onRangeChange={onRangeChange}
    />
  );

  return (
    <>
      <section className={styles.card}>
        <div className={styles.head}>
          <div>
            <h3 className={styles.title}>지수</h3>
            <p className={styles.subtitle}>왼쪽 국장 · 오른쪽 미장</p>
          </div>
        </div>
        {/* 2열: 국장(해당 지수) | 미장(나스닥) */}
        <div className={styles.grid}>
          {krIndex ? card(krIndex, "kr-idx") : <div className={styles.slot} />}
          {nasdaq ? card(nasdaq, "us-idx") : <div className={styles.slot} />}
        </div>
      </section>

      {meta.kr_etf || meta.us_etf ? (
        <section className={styles.card}>
          <div className={styles.head}>
            <div>
              <h3 className={styles.title}>섹터 ETF</h3>
              <p className={styles.subtitle}>왼쪽 국장 · 오른쪽 미장</p>
            </div>
          </div>
          {/* 2열: 국장 ETF | 미장 ETF */}
          <div className={styles.grid}>
            {meta.kr_etf ? card(meta.kr_etf, "kr-etf") : <div className={styles.slot} />}
            {meta.us_etf ? card(meta.us_etf, "us-etf") : <div className={styles.slot} />}
          </div>
        </section>
      ) : null}
    </>
  );
}
