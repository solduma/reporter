"use client";

import { useEffect, useState } from "react";

import SymbolChartCard from "@/components/SymbolChartCard";
import { fetchSectorStocks } from "@/lib/api";
import type { ChartTimeframe, FlowMarket, SectorStock, SectorStockSort } from "@/lib/types";

import styles from "./SectorTopStockCharts.module.css";

const TOP_N = 4; // 시총/거래대금 상위 4개만 — 화면을 간결하게 유지

type State = {
  status: "loading" | "ready" | "error";
  stocks: SectorStock[];
  message?: string;
};

function changeClass(rising: boolean | null): string {
  if (rising === true) {
    return styles.up;
  }
  if (rising === false) {
    return styles.down;
  }
  return styles.flat;
}

function priceMeta(stock: SectorStock) {
  return (
    <span className={changeClass(stock.rising)}>
      {stock.close ?? "—"}
      {stock.change_ratio ? <span className={styles.ratio}>{stock.change_ratio}%</span> : null}
    </span>
  );
}

// timeframe 은 부모(페이지)의 공용 슬라이더가 제어한다 — 지수·ETF·종목 차트가 함께 조정된다.
export default function SectorTopStockCharts({
  industry,
  timeframe,
}: {
  industry: string;
  timeframe: ChartTimeframe;
}) {
  const [market, setMarket] = useState<FlowMarket>("KR");
  const [sort, setSort] = useState<SectorStockSort>("cap");
  const [state, setState] = useState<State>({ status: "loading", stocks: [] });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", stocks: [] });
      try {
        // 백엔드가 sort(cap=시총 desc) 순으로 정렬 반환 → 상위 TOP_N 만 요청.
        const page = await fetchSectorStocks(industry, market, { sort, limit: TOP_N });
        if (!active) {
          return;
        }
        setState({ status: "ready", stocks: page });
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            stocks: [],
            message: e instanceof Error ? e.message : "종목 목록을 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [industry, market, sort]);

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <div>
          <h2 className={styles.title}>소속 종목 차트</h2>
          <p className={styles.subtitle}>시총·거래대금 상위 종목 흐름</p>
        </div>
        <div className={styles.controls}>
          <div className={styles.tabs} role="tablist" aria-label="시장 선택">
            {(["KR", "US"] as FlowMarket[]).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={m === market}
                className={m === market ? `${styles.tab} ${styles.tabActive}` : styles.tab}
                onClick={() => setMarket(m)}
              >
                {m === "KR" ? "국장" : "미장"}
              </button>
            ))}
          </div>
          <div className={styles.tabs} role="tablist" aria-label="정렬 기준">
            {(["cap", "value"] as SectorStockSort[]).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={s === sort}
                className={s === sort ? `${styles.tab} ${styles.tabActive}` : styles.tab}
                onClick={() => setSort(s)}
              >
                {s === "cap" ? "시총" : "거래대금"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {state.status === "loading" ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : state.status === "error" && state.stocks.length === 0 ? (
        <p className={styles.error}>API 연결 실패: {state.message}</p>
      ) : state.stocks.length === 0 ? (
        <p className={styles.status}>
          {market === "KR"
            ? "이 섹터에 매칭되는 종목이 없습니다."
            : "대응하는 미국 섹터 종목이 없습니다."}
        </p>
      ) : (
        <div className={styles.chartGrid}>
          {state.stocks
            .filter((s) => s.symbol)
            .slice(0, TOP_N)
            .map((s) => (
              <SymbolChartCard
                key={s.symbol}
                symbol={s.symbol as string}
                market={s.market === "US" ? "US" : "KR"}
                timeframe={timeframe}
                label={s.name}
                href={s.code ? `/companies/${s.code}` : undefined}
                meta={priceMeta(s)}
              />
            ))}
        </div>
      )}
    </section>
  );
}
