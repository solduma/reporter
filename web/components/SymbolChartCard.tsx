"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { fetchChart } from "@/lib/api";
import type { CandlePoint, ChartTimeframe, FlowMarket } from "@/lib/types";

import styles from "./SymbolChartCard.module.css";

// lightweight-charts는 캔버스 기반 브라우저 전용이라 SSR을 끈다(회사 상세 페이지와 동일).
const CandleChart = dynamic(() => import("@/components/CandleChart"), {
  ssr: false,
  loading: () => <div className={styles.status}>차트 불러오는 중…</div>,
});

interface Props {
  symbol: string;
  market: FlowMarket;
  timeframe: ChartTimeframe;
  label: string;
  href?: string; // 클릭 시 이동할 경로(국내 종목 → /companies/{code})
  meta?: ReactNode; // 헤더 우측 시세/등락 뱃지 등
  height?: number;
}

type State = {
  status: "loading" | "ready" | "error";
  data: CandlePoint[];
  message?: string;
};

const CHART_HEIGHT = 240;

export default function SymbolChartCard({
  symbol,
  market,
  timeframe,
  label,
  href,
  meta,
  height = CHART_HEIGHT,
}: Props) {
  const [state, setState] = useState<State>({ status: "loading", data: [] });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", data: [] });
      try {
        const data = await fetchChart(symbol, market, timeframe);
        if (active) {
          setState({ status: "ready", data });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "차트를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [symbol, market, timeframe]);

  const title = href ? (
    <Link href={href} className={styles.titleLink}>
      {label}
    </Link>
  ) : (
    <span className={styles.title}>{label}</span>
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <div className={styles.status} style={{ height }} />;
  } else if (state.status === "error") {
    body = (
      <div className={styles.status} style={{ height }}>
        API 연결 실패: {state.message}
      </div>
    );
  } else if (state.data.length === 0) {
    body = (
      <div className={styles.status} style={{ height }}>
        차트 데이터가 없습니다
      </div>
    );
  } else {
    body = <CandleChart data={state.data} timeframe={timeframe} height={height} />;
  }

  return (
    <div className={styles.card}>
      <div className={styles.head}>
        {title}
        {meta ? <span className={styles.meta}>{meta}</span> : null}
      </div>
      {body}
    </div>
  );
}
