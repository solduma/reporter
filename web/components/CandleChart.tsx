"use client";

import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  HistogramSeries,
  LineSeries,
} from "lightweight-charts";
import type {
  CandlestickData,
  HistogramData,
  LineData,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import type { CandlePoint, Timeframe } from "@/lib/types";

import styles from "./CandleChart.module.css";

// 한국 시장 관례: 상승 빨강 · 하락 파랑. globals.css 토큰을 SVG/캔버스가 해석하지 못해 직접 값을 둔다.
const COLOR_UP = "#c02b2b";
const COLOR_DOWN = "#2b6cc0";
const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";
// 이동평균선(10·20·120)
const MA_DEFS: { period: number; color: string }[] = [
  { period: 10, color: "#e8a33d" },
  { period: 20, color: "#2ca089" },
  { period: 120, color: "#8b5cf6" },
];

interface Props {
  data: CandlePoint[];
  timeframe: Timeframe;
  height?: number; // 그리드용 소형 차트를 위해 컨테이너 높이를 조절(기본 420)
}

// 30분봉의 t는 타임존 없는 벽시계 시각이라, UTC로 간주해 표기 시각이 그대로 보이도록 한다.
function toChartTime(point: CandlePoint, tf: Timeframe): Time {
  if (tf === "30m") {
    return (Date.parse(`${point.t}Z`) / 1000) as UTCTimestamp;
  }
  return point.t.slice(0, 10);
}

// 종가 단순이동평균. 구간 미달 구간은 건너뛴다(선이 그 시점부터 시작).
function movingAverage(data: CandlePoint[], tf: Timeframe, period: number): LineData[] {
  const out: LineData[] = [];
  let sum = 0;
  for (let i = 0; i < data.length; i += 1) {
    sum += data[i].c;
    if (i >= period) {
      sum -= data[i - period].c;
    }
    if (i >= period - 1) {
      out.push({ time: toChartTime(data[i], tf), value: sum / period });
    }
  }
  return out;
}

export default function CandleChart({ data, timeframe, height = 420 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [logScale, setLogScale] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: COLOR_TEXT,
        fontFamily: "inherit",
      },
      grid: {
        vertLines: { color: COLOR_GRID },
        horzLines: { color: COLOR_GRID },
      },
      rightPriceScale: {
        borderColor: COLOR_GRID,
        mode: logScale ? 1 : 0, // 1 = Logarithmic
      },
      timeScale: {
        borderColor: COLOR_GRID,
        timeVisible: timeframe === "30m",
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR" },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLOR_UP,
      downColor: COLOR_DOWN,
      wickUpColor: COLOR_UP,
      wickDownColor: COLOR_DOWN,
      borderVisible: false,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const candles: CandlestickData[] = data.map((point) => ({
      time: toChartTime(point, timeframe),
      open: point.o,
      high: point.h,
      low: point.low,
      close: point.c,
    }));
    const volumes: HistogramData[] = data.map((point) => ({
      time: toChartTime(point, timeframe),
      value: point.v,
      color: point.c >= point.o ? COLOR_UP : COLOR_DOWN,
    }));

    candleSeries.setData(candles);
    volumeSeries.setData(volumes);

    // 이동평균선 오버레이(데이터가 충분한 기간만).
    for (const { period, color } of MA_DEFS) {
      if (data.length < period) {
        continue;
      }
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(movingAverage(data, timeframe, period));
    }

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
    };
  }, [data, timeframe, logScale]);

  return (
    <div className={styles.wrap}>
      <div className={styles.controls}>
        <div className={styles.maLegend}>
          {MA_DEFS.map((m) => (
            <span key={m.period} className={styles.maItem}>
              <span className={styles.maDot} style={{ background: m.color }} />
              MA{m.period}
            </span>
          ))}
        </div>
        <button
          type="button"
          className={logScale ? `${styles.logBtn} ${styles.logBtnActive}` : styles.logBtn}
          onClick={() => setLogScale((v) => !v)}
          aria-pressed={logScale}
        >
          로그
        </button>
      </div>
      <div ref={containerRef} className={styles.chart} style={{ height }} />
    </div>
  );
}
