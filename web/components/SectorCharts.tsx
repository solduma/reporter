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

// timeframe 은 부모(페이지)의 공용 슬라이더가 제어한다 — 지수·ETF·종목 차트가 함께 조정된다.
export default function SectorCharts({
  industry,
  timeframe,
}: {
  industry: string;
  timeframe: ChartTimeframe;
}) {
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

  const etfs = [meta.kr_etf, meta.us_etf].filter((r): r is ChartRef => r !== null);

  return (
    <>
      <section className={styles.card}>
        <div className={styles.head}>
          <div>
            <h2 className={styles.title}>지수 흐름</h2>
            <p className={styles.subtitle}>국내(코스피·코스닥) · 미국(나스닥100·러셀2000)</p>
          </div>
        </div>
        <div className={styles.grid}>
          {meta.indices.map((ref) => (
            <SymbolChartCard
              key={`${ref.market}-${ref.symbol}`}
              symbol={ref.symbol}
              market={ref.market}
              timeframe={timeframe}
              label={ref.label}
            />
          ))}
        </div>
      </section>

      {etfs.length > 0 ? (
        <section className={styles.card}>
          <div className={styles.head}>
            <div>
              <h2 className={styles.title}>섹터 추종 ETF</h2>
              <p className={styles.subtitle}>이 섹터를 추종하는 국내·미국 ETF 흐름</p>
            </div>
          </div>
          <div className={styles.grid}>
            {etfs.map((ref) => (
              <SymbolChartCard
                key={`${ref.market}-${ref.symbol}`}
                symbol={ref.symbol}
                market={ref.market}
                timeframe={timeframe}
                label={ref.label}
              />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
