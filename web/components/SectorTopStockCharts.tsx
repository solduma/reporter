"use client";

import { useCallback, useEffect, useState } from "react";

import SymbolChartCard from "@/components/SymbolChartCard";
import { fetchSectorStocks } from "@/lib/api";
import type { ChartTimeframe, FlowMarket, SectorStock, SectorStockSort } from "@/lib/types";

import styles from "./SectorTopStockCharts.module.css";

const PAGE_SIZE = 10;
const TIMEFRAME: ChartTimeframe = "day"; // 종목 캔들은 일봉 고정(그리드가 조밀함)

type State = {
  status: "loading" | "ready" | "error";
  stocks: SectorStock[];
  message?: string;
  hasMore: boolean;
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

export default function SectorTopStockCharts({ industry }: { industry: string }) {
  const [market, setMarket] = useState<FlowMarket>("KR");
  const [sort, setSort] = useState<SectorStockSort>("cap");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<State>({ status: "loading", stocks: [], hasMore: false });

  // 시장/정렬이 바뀌면 처음부터 다시 그린다.
  useEffect(() => {
    setOffset(0);
  }, [industry, market, sort]);

  useEffect(() => {
    let active = true;
    async function load() {
      // offset 0(새 조합)이면 로딩 표시, 더보기(offset>0)면 기존 목록 유지.
      if (offset === 0) {
        setState({ status: "loading", stocks: [], hasMore: false });
      }
      try {
        const page = await fetchSectorStocks(industry, market, {
          sort,
          limit: PAGE_SIZE,
          offset,
        });
        if (!active) {
          return;
        }
        setState((prev) => ({
          status: "ready",
          stocks: offset === 0 ? page : [...prev.stocks, ...page],
          hasMore: page.length === PAGE_SIZE,
        }));
      } catch (e) {
        if (active) {
          setState((prev) => ({
            status: "error",
            stocks: prev.stocks,
            message: e instanceof Error ? e.message : "종목 목록을 불러오지 못했습니다",
            hasMore: false,
          }));
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [industry, market, sort, offset]);

  const loadMore = useCallback(() => {
    setOffset((o) => o + PAGE_SIZE);
  }, []);

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
        <>
          <div className={styles.chartGrid}>
            {state.stocks
              .filter((s) => s.symbol)
              .map((s) => (
                <SymbolChartCard
                  key={s.symbol}
                  symbol={s.symbol as string}
                  market={s.market === "US" ? "US" : "KR"}
                  timeframe={TIMEFRAME}
                  label={s.name}
                  href={s.code ? `/companies/${s.code}` : undefined}
                  meta={priceMeta(s)}
                />
              ))}
          </div>
          {state.hasMore ? (
            <div className={styles.moreRow}>
              <button type="button" className={styles.moreBtn} onClick={loadMore}>
                더보기
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
