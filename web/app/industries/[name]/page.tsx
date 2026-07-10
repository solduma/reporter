"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import BroadcastRail from "@/components/BroadcastRail";
import ReportRefDrawer from "@/components/ReportRefDrawer";
import SectorCharts from "@/components/SectorCharts";
import SectorFlowDetailCard from "@/components/SectorFlowDetailCard";
import SectorStockList from "@/components/SectorStockList";
import SectorTopStockCharts from "@/components/SectorTopStockCharts";
import SentimentChart from "@/components/SentimentChart";
import TimeframeSlider from "@/components/TimeframeSlider";
import { fetchIndustrySentiment } from "@/lib/api";
import type { ChartTimeframe, SentimentPoint } from "@/lib/types";

import styles from "./page.module.css";

export default function SectorDetailPage({ params }: { params: { name: string } }) {
  const sector = decodeURIComponent(params.name);
  const [points, setPoints] = useState<SentimentPoint[]>([]);
  const [selectedPoint, setSelectedPoint] = useState<SentimentPoint | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  // 지수·ETF·종목 차트가 공유하는 기간. 슬라이더 하나로 모든 차트를 함께 조정한다.
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("day");

  useEffect(() => {
    let active = true;
    async function load() {
      setStatus("loading");
      setMessage(null);
      setSelectedPoint(null);
      try {
        const res = await fetchIndustrySentiment(sector);
        if (active) {
          setPoints(res);
          setStatus("ready");
        }
      } catch (e) {
        if (active) {
          setStatus("error");
          setMessage(e instanceof Error ? e.message : "센티먼트 데이터를 불러오지 못했습니다");
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [sector]);

  const chartArea = useMemo(() => {
    if (status === "loading") {
      return <div className={styles.chartStatus}>불러오는 중…</div>;
    }
    if (status === "error") {
      return <div className={styles.chartStatus}>{message}</div>;
    }
    if (points.length === 0) {
      return <div className={styles.chartStatus}>이 섹터의 센티먼트 데이터가 없습니다</div>;
    }
    return (
      <SentimentChart
        data={points}
        selectedDate={selectedPoint?.date ?? null}
        onSelectPoint={setSelectedPoint}
      />
    );
  }, [status, message, points, selectedPoint]);

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <div className={styles.breadcrumb}>
          <Link href="/industries" className={styles.back}>
            ← 산업 흐름
          </Link>
        </div>
        <h1 className={styles.title}>{sector}</h1>
        <p className={styles.subtitle}>
          투자의견(BUY +1 · HOLD 0 · SELL −1) 추이 · 관련 리포트 · 브리핑
        </p>
      </header>

      <div className={styles.layout}>
        <section className={styles.chartCard}>
          <div className={styles.chartHead}>
            <h2 className={styles.chartTitle}>센티먼트 추이</h2>
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

      <SectorFlowDetailCard industry={sector} />

      <div className={styles.chartControls}>
        <TimeframeSlider value={timeframe} onChange={setTimeframe} label="차트 기간" />
      </div>

      <SectorCharts industry={sector} timeframe={timeframe} />

      <SectorTopStockCharts industry={sector} timeframe={timeframe} />

      <SectorStockList industry={sector} />

      <BroadcastRail
        query={{ industry: sector }}
        title={`📣 ${sector} 관련 브리핑`}
        emptyText="이 섹터를 언급한 텔레그램 브리핑이 아직 없습니다. (배포 이후 발송분부터 축적됩니다)"
      />
    </div>
  );
}
