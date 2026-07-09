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

const TF_TABS: { id: ChartTimeframe; label: string }[] = [
  { id: "day", label: "일" },
  { id: "week", label: "주" },
];

export default function SectorCharts({ industry }: { industry: string }) {
  const [tf, setTf] = useState<ChartTimeframe>("day");
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
          <div className={styles.tabs} role="tablist" aria-label="기간 선택">
            {TF_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={t.id === tf}
                className={t.id === tf ? `${styles.tab} ${styles.tabActive}` : styles.tab}
                onClick={() => setTf(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.grid}>
          {meta.indices.map((ref) => (
            <SymbolChartCard
              key={`${ref.market}-${ref.symbol}`}
              symbol={ref.symbol}
              market={ref.market}
              timeframe={tf}
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
                timeframe={tf}
                label={ref.label}
              />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
