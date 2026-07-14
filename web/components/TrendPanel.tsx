"use client";

import { BaselineSeries, ColorType, createChart } from "lightweight-charts";
import type { IChartApi, Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

import InfoDot from "@/components/InfoDot";
import type { CompanyTrend, RelStrengthPoint, StageFrame } from "@/lib/types";

import styles from "./TrendPanel.module.css";

// 프레임별 표시: 지평명 + 의도 기간 + 봉단위·MA. 사용자가 각 국면이 어느 시간축인지 알게 한다.
const FRAME_META: Record<string, { label: string; span: string }> = {
  short: { label: "단기", span: "6개월 이내" },
  mid: { label: "중기", span: "6개월~2년" },
  long: { label: "장기", span: "2~10년" },
};
const BAR_LABEL: Record<string, string> = { day: "일봉", week: "주봉", month: "월봉" };
// 국면별 표시 스타일(배지 색). 2=매수존, 4=회피.
const STAGE_STYLE: Record<number, { cls: string; hint: string }> = {
  1: { cls: styles.stage1, hint: "매집·관망" },
  2: { cls: styles.stage2, hint: "상승·매수존" },
  3: { cls: styles.stage3, hint: "분산·주의" },
  4: { cls: styles.stage4, hint: "하락·회피" },
};
// 볼륨 축적/분산 칩(거래량 시그니처). 축적=상승에 거래량(bullish), 분산=하락에 거래량(bearish).
const VOL_CHIP: Record<string, { label: string; cls: string }> = {
  accumulation: { label: "축적", cls: styles.volAccum },
  distribution: { label: "분산", cls: styles.volDistrib },
};
// 변동성 레짐 칩. 수축=베이스 다지기(바닥 성격), 확장=돌파/클라이맥스(천정 성격).
const VOLATILITY_CHIP: Record<string, string> = { contraction: "수축", expansion: "확장" };
// 돌파 칩 — 볼륨 확인된 신 N기간 고/저 돌파(Weinstein 저항/지지 트리거).
const BREAKOUT_CHIP: Record<string, { label: string; cls: string }> = {
  up: { label: "↑돌파", cls: styles.volAccum },
  down: { label: "↓이탈", cls: styles.volDistrib },
};
// 스윙 구조 칩(HH/HL/LH/LL 관계). up=상승구조, down=하락구조, range=엇갈림.
const STRUCTURE_CHIP: Record<string, string> = { up: "구조↑", down: "구조↓", range: "구조↔" };
// 국면 전환 조짐 타점 — 저점 상향(HL)=매수 조짐, 고점 하향(LH)=매도 조짐.
const SETUP_CHIP: Record<string, { label: string; cls: string }> = {
  stage1_to_2: { label: "매수 조짐(HL)", cls: styles.volAccum },
  stage3_to_4: { label: "매도 조짐(LH)", cls: styles.volDistrib },
};
// 박스권 돌파/이탈 타점 — 상단 돌파=매수, 하단 이탈=매도(거래량 확정 시 강조).
const BOX_CHIP: Record<string, { label: string; cls: string }> = {
  breakout: { label: "박스 상단 돌파", cls: styles.volAccum },
  breakdown: { label: "박스 하단 이탈", cls: styles.volDistrib },
};

function StageBadge({ f }: { f: StageFrame }) {
  const style = f.stage ? STAGE_STYLE[f.stage] : null;
  const meta = FRAME_META[f.frame];
  const vol = f.volume_signal ? VOL_CHIP[f.volume_signal] : null;
  const volat = f.volatility ? VOLATILITY_CHIP[f.volatility] : null;
  const brk = f.breakout ? BREAKOUT_CHIP[f.breakout] : null;
  const struct = f.structure && f.structure !== "none" ? STRUCTURE_CHIP[f.structure] : null;
  const setup = f.setup && f.setup !== "none" ? SETUP_CHIP[f.setup] : null;
  const box = f.box_event && (f.box_event === "breakout" || f.box_event === "breakdown")
    ? BOX_CHIP[f.box_event]
    : null;
  return (
    <div className={styles.stageItem}>
      <span className={styles.frameLabel}>
        {meta.label} <span className={styles.frameSpan}>{meta.span}</span>
        {f.low_confidence ? <span className={styles.lowConf}>이력 부족</span> : null}
      </span>
      <span className={styles.stageLine}>
        <span className={`${styles.stageBadge} ${style?.cls ?? styles.stageNa}`}>
          {f.label ?? "—"}
        </span>
        {vol ? <span className={`${styles.volChip} ${vol.cls}`}>{vol.label}</span> : null}
        {volat ? <span className={styles.volatChip}>{volat}</span> : null}
        {brk ? <span className={`${styles.volChip} ${brk.cls}`}>{brk.label}</span> : null}
        {struct ? <span className={styles.volatChip}>{struct}</span> : null}
        {setup ? <span className={`${styles.volChip} ${setup.cls}`}>{setup.label}</span> : null}
        {box ? (
          <span className={`${styles.volChip} ${box.cls}`}>
            {box.label}{f.box_vol_confirmed ? " ⚡" : ""}
          </span>
        ) : null}
      </span>
      {style ? <span className={styles.stageHint}>{style.hint}</span> : null}
      {typeof f.channel_pos === "number" ? (
        <span className={styles.channelBar} title={`레인지 내 위치 ${Math.round(f.channel_pos)}%`}>
          <span className={styles.channelFill} style={{ width: `${f.channel_pos}%` }} />
        </span>
      ) : null}
      <span className={styles.maPeriod}>
        {BAR_LABEL[f.bar]} MA{f.period}
        {f.quality !== null ? ` · 신뢰 ${Math.round(f.quality)}` : ""}
      </span>
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
            what="주가가 추세상 어느 국면(바닥→상승→천정→하락)에 있는지. 가격·이평 위치·곡선 모양·거래량으로 판별."
            guide="② 상승이 매수존, ④ 하락은 회피. 단기=일봉·중기=주봉(와인스타인 30주)·장기=월봉. 축적/분산=거래량 방향, 수축/확장=변동성, ↑돌파=볼륨 실린 신고가 돌파. 아래 바=기간 레인지 내 위치(우측=고점권). '이력 부족'은 데이터 짧아 신뢰도 낮음."
          />
        </div>
        <div className={styles.stages}>
          {trend.stages.map((f) => (
            <StageBadge key={f.frame} f={f} />
          ))}
        </div>
        {trend.secular && trend.secular.ma_months && trend.secular.ratio !== null ? (
          <p className={styles.secular}>
            장기 평균({trend.secular.ma_months}개월) 대비{" "}
            <span
              className={
                trend.secular.position === "above"
                  ? styles.rsPos
                  : trend.secular.position === "below"
                    ? styles.rsNeg
                    : undefined
              }
            >
              {trend.secular.ratio >= 0 ? "+" : ""}
              {(trend.secular.ratio * 100).toFixed(0)}%
            </span>
            {trend.secular.ma_dir === "rising"
              ? " · 장기 상승"
              : trend.secular.ma_dir === "falling"
                ? " · 장기 하락"
                : " · 장기 횡보"}
          </p>
        ) : null}
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

      {trend.elliott ? (
        <div className={styles.elliottRow}>
          <div className={styles.blockHead}>
            <span className={styles.blockTitle}>엘리엇 파동 (추정)</span>
            <InfoDot
              what="추진 5파(1-2-3-4-5, 남색)와 조정 3파(A-B-C, 보라)가 번갈아 끊김 없이 이어지는 반복 사이클을 차트에 라벨. 하락 추세면 추진은 하락 5파·조정은 상승 3파(방향 반전)."
              guide="해석이 갈리는 보조지표라 참고용. 3대 하드룰+피보를 통과한 구간은 진한 실선 + 번호 라벨(고신뢰), 형태가 덜 맞는 연결 구간은 옅은 점선(저신뢰)으로 차등 표시합니다. 확정 신호가 아니며 매매 판단 근거로 삼지 마세요."
            />
            {trend.elliott.labeled ? (
              <span className={styles.elliottBadge}>
                {trend.elliott.direction === "down" ? "하락" : "상승"} 추세 · 신뢰도{" "}
                {Math.round(trend.elliott.confidence * 100)}%
              </span>
            ) : (
              <span className={styles.elliottBadgeMuted}>뚜렷한 파동 없음</span>
            )}
          </div>
          {trend.elliott.current_position ? (
            <p className={styles.elliottPosition}>
              <span className={styles.elliottPositionLabel}>현재 위치</span>
              {trend.elliott.current_position}
              {typeof trend.elliott.invalidation_price === "number" ? (
                <span className={styles.elliottInval}>
                  무효화 {trend.elliott.invalidation_price.toLocaleString("ko-KR")}
                </span>
              ) : null}
            </p>
          ) : null}
          {trend.elliott.projection ? (
            <p className={styles.elliottProjection}>
              <span className={styles.elliottProjectionLabel}>
                {trend.elliott.projection.wave}
              </span>
              {trend.elliott.projection.low.toLocaleString("ko-KR")}~
              {trend.elliott.projection.high.toLocaleString("ko-KR")}
              {trend.elliott.projection.bars_low && trend.elliott.projection.bars_high ? (
                <span className={styles.elliottProjectionBasis}>
                  {trend.elliott.projection.bars_low}~{trend.elliott.projection.bars_high}봉 ·{" "}
                  {trend.elliott.projection.basis}
                </span>
              ) : (
                <span className={styles.elliottProjectionBasis}>
                  {trend.elliott.projection.basis}
                </span>
              )}
            </p>
          ) : null}
          <p className={styles.elliottNote}>{trend.elliott.note}</p>
        </div>
      ) : null}
    </div>
  );
}
