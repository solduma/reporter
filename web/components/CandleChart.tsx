"use client";

import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  HistogramSeries,
} from "lightweight-charts";
import type {
  CandlestickData,
  HistogramData,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { CandlePoint, Timeframe } from "@/lib/types";

import styles from "./CandleChart.module.css";

// 한국 시장 관례: 상승 빨강 · 하락 파랑. globals.css 토큰을 SVG/캔버스가 해석하지 못해 직접 값을 둔다.
const COLOR_UP = "#c02b2b";
const COLOR_DOWN = "#2b6cc0";
const COLOR_AXIS = "#6b7280";
const COLOR_GRID = "#e4e7eb";
const COLOR_TEXT = "#1a1d21";

interface Props {
  data: CandlePoint[];
  timeframe: Timeframe;
}

// 30분봉의 t는 타임존 없는 벽시계 시각이라, UTC로 간주해 표기 시각이 그대로 보이도록 한다.
function toChartTime(point: CandlePoint, tf: Timeframe): Time {
  if (tf === "30m") {
    return (Date.parse(`${point.t}Z`) / 1000) as UTCTimestamp;
  }
  return point.t.slice(0, 10);
}

export default function CandleChart({ data, timeframe }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

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
      rightPriceScale: { borderColor: COLOR_GRID },
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
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
    };
  }, [data, timeframe]);

  return <div ref={containerRef} className={styles.chart} />;
}
