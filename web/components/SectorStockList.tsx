"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchSectorStocks } from "@/lib/api";
import type { FlowMarket, SectorStock } from "@/lib/types";

import styles from "./SectorStockList.module.css";

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

function StockRow({ stock }: { stock: SectorStock }) {
  const change = (
    <span className={`${styles.change} ${changeClass(stock.rising)}`}>
      {stock.close ?? "—"}
      {stock.change_ratio ? <span className={styles.ratio}>{stock.change_ratio}%</span> : null}
    </span>
  );
  // 국내 종목은 코드로 종목분석 이동. 미국은 심볼(NVDA.O)에서 접미사를 떼 /us/{ticker} 로 이동.
  if (stock.code) {
    return (
      <Link href={`/companies/${stock.code}`} className={`${styles.row} ${styles.linkRow}`}>
        <span className={styles.name}>{stock.name}</span>
        <span className={styles.code}>{stock.code}</span>
        {change}
      </Link>
    );
  }
  if (stock.market === "US" && stock.symbol) {
    const ticker = stock.symbol.split(".")[0]; // NVDA.O → NVDA
    return (
      <Link href={`/us/${ticker}`} className={`${styles.row} ${styles.linkRow}`}>
        <span className={styles.name}>{stock.name}</span>
        <span className={styles.code}>{ticker}</span>
        {change}
      </Link>
    );
  }
  return (
    <div className={styles.row}>
      <span className={styles.name}>{stock.name}</span>
      <span className={styles.code} />
      {change}
    </div>
  );
}

export default function SectorStockList({ industry }: { industry: string }) {
  const [market, setMarket] = useState<FlowMarket>("KR");
  const [state, setState] = useState<State>({ status: "loading", stocks: [] });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", stocks: [] });
      try {
        const stocks = await fetchSectorStocks(industry, market);
        if (active) {
          setState({ status: "ready", stocks });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            stocks: [],
            message: e instanceof Error ? e.message : "종목 명단을 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [industry, market]);

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h2 className={styles.title}>소속 종목</h2>
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
      </div>

      {state.status === "loading" ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : state.status === "error" ? (
        <p className={styles.error}>API 연결 실패: {state.message}</p>
      ) : state.stocks.length === 0 ? (
        <p className={styles.status}>
          {market === "KR"
            ? "이 섹터에 매칭되는 종목이 없습니다."
            : "대응하는 미국 섹터 종목이 없습니다."}
        </p>
      ) : (
        <div className={styles.list}>
          {state.stocks.map((stock) => (
            <StockRow key={stock.code ?? stock.name} stock={stock} />
          ))}
        </div>
      )}
    </section>
  );
}
