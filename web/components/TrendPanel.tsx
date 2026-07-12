"use client";

import { BaselineSeries, ColorType, createChart } from "lightweight-charts";
import type { IChartApi, Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

import InfoDot from "@/components/InfoDot";
import type { CompanyTrend, RelStrengthPoint, StageFrame } from "@/lib/types";

import styles from "./TrendPanel.module.css";

const FRAME_LABEL: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
// 국면별 표시 스타일(배지 색). 2=매수존, 4=회피.
const STAGE_STYLE: Record<number, { cls: string; hint: string }> = {
  1: { cls: styles.stage1, hint: "매집·관망" },
  2: { cls: styles.stage2, hint: "상승·매수존" },
  3: { cls: styles.stage3, hint: "분산·주의" },
  4: { cls: styles.stage4, hint: "하락·회피" },
};

function StageBadge({ f }: { f: StageFrame }) {
  const style = f.stage ? STAGE_STYLE[f.stage] : null;
  return (
    <div className={styles.stageItem}>
      <span className={styles.frameLabel}>
        {FRAME_LABEL[f.frame]} <span className={styles.maPeriod}>MA{f.period}</span>
      </span>
      <span className={`${styles.stageBadge} ${style?.cls ?? styles.stageNa}`}>
        {f.label ?? "—"}
      </span>
      {style ? <span className={styles.stageHint}>{style.hint}</span> : null}
    </div>
  );
}

// Mansfield 상대강도(0중심) 미니 차트. 0선 위=지수 아웃퍼폼(빨강), 아래=언더퍼폼(파랑).
function RsChart({ series }: { series: RelStrengthPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || series.length === 0) {
      return;
    }
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#1a1d21", fontFamily: "inherit" },
      grid: { vertLines: { color: "#e4e7eb" }, horzLines: { color: "#e4e7eb" } },
      rightPriceScale: { borderColor: "#e4e7eb" },
      timeScale: { borderColor: "#e4e7eb" },
      localization: { locale: "ko-KR" },
    });
    const rs = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 0 },
      topLineColor: "#c02b2b",
      topFillColor1: "rgba(192,43,43,0.20)",
      topFillColor2: "rgba(192,43,43,0.02)",
      bottomLineColor: "#2b6cc0",
      bottomFillColor1: "rgba(43,108,192,0.02)",
      bottomFillColor2: "rgba(43,108,192,0.20)",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    rs.setData(series.map((p) => ({ time: p.date as Time, value: p.value })));
    chart.timeScale().fitContent();
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [series]);

  if (series.length === 0) {
    return <div className={styles.rsEmpty}>상대강도 데이터가 부족합니다(200거래일 이상 필요)</div>;
  }
  return <div ref={ref} className={styles.rsChart} />;
}

interface Props {
  trend: CompanyTrend | null;
  status: "loading" | "ready" | "error";
  message?: string;
}

export default function TrendPanel({ trend, status, message }: Props) {
  if (status === "loading") {
    return <div className={styles.status}>추세 계산 중…</div>;
  }
  if (status === "error") {
    return <p className={styles.error}>API 연결 실패: {message}</p>;
  }
  if (!trend) {
    return <div className={styles.status}>추세 데이터가 없습니다</div>;
  }

  const rsLatest = trend.rs_latest;
  const rating = trend.rs_rating;
  return (
    <div className={styles.panel}>
      {rating !== null && rating !== undefined ? (
        <div className={styles.ratingRow}>
          <div className={styles.blockHead}>
            <span className={styles.blockTitle}>RS Rating</span>
            <InfoDot
              what="전체 종목 대비 최근 1년 가격 상승세 순위(1~99, IBD 방식)."
              guide="99=상위 1% 주도주. 80↑이 실전 매수 후보. 최근 분기를 2배 가중."
            />
          </div>
          <div className={styles.ratingGauge}>
            <span
              className={`${styles.ratingValue} ${rating >= 80 ? styles.rsPos : rating < 40 ? styles.rsNeg : ""}`}
            >
              {rating}
            </span>
            <span className={styles.ratingMax}>/99</span>
            <div className={styles.ratingBar}>
              <div className={styles.ratingFill} style={{ width: `${rating}%` }} />
            </div>
          </div>
        </div>
      ) : null}

      <div className={styles.stageRow}>
        <div className={styles.blockHead}>
          <span className={styles.blockTitle}>와인스타인 국면</span>
          <InfoDot
            what="주가가 30주 이동평균 대비 어느 국면(바닥→상승→천정→하락)에 있는지."
            guide="② 상승이 매수존, ④ 하락은 회피. 단·중·장기(MA50/150/200)로 함께 본다."
          />
        </div>
        <div className={styles.stages}>
          {trend.stages.map((f) => (
            <StageBadge key={f.frame} f={f} />
          ))}
        </div>
      </div>

      <div className={styles.rsRow}>
        <div className={styles.blockHead}>
          <span className={styles.blockTitle}>
            상대강도 (Mansfield · {trend.benchmark} 대비)
          </span>
          <InfoDot
            what="종목이 시장 지수보다 강한지 약한지(RSI 아님). 0선 기준 초과성과."
            guide="0 위=지수보다 강함(주도주 후보), 0 아래=약함. 0선 상향 돌파가 강세 전환 신호."
          />
          {rsLatest !== null ? (
            <span
              className={`${styles.rsValue} ${rsLatest >= 0 ? styles.rsPos : styles.rsNeg}`}
            >
              {rsLatest >= 0 ? "+" : ""}
              {rsLatest.toFixed(1)} · {trend.rs_outperforming ? "아웃퍼폼" : "언더퍼폼"}
            </span>
          ) : null}
        </div>
        <RsChart series={trend.rs_series} />
      </div>
    </div>
  );
}
