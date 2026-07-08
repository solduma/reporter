"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchMarketOverview } from "@/lib/api";
import type { MarketOverview, UsIndex } from "@/lib/types";

import styles from "./MarketOverviewCard.module.css";

// 알려진 HS 코드 → 한글 품목명. 없으면 코드 그대로 노출한다.
const HS_NAMES: Record<string, string> = {
  "8542": "반도체",
  "8471": "컴퓨터",
  "8517": "통신기기",
  "2710": "석유제품",
  "8703": "승용차",
  "8708": "자동차부품",
};

// 나스닥은 성장주 프록시라 타일을 강조한다.
const EMPHASIS_INDEX = "나스닥";

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  });
}

function directionClass(rising: boolean | null): string {
  if (rising === true) {
    return styles.up;
  }
  if (rising === false) {
    return styles.down;
  }
  return styles.flat;
}

function directionArrow(rising: boolean | null): string {
  if (rising === true) {
    return "▲";
  }
  if (rising === false) {
    return "▼";
  }
  return "–";
}

// 부호 없는 표시 문자열에 방향 부호를 붙인다. 이미 부호가 있으면 그대로 둔다.
function signedRatio(ratio: string, rising: boolean | null): string {
  const trimmed = ratio.trim();
  if (!trimmed || trimmed.startsWith("+") || trimmed.startsWith("-") || trimmed.startsWith("−")) {
    return `${trimmed}%`;
  }
  if (rising === true) {
    return `+${trimmed}%`;
  }
  if (rising === false) {
    return `−${trimmed}%`;
  }
  return `${trimmed}%`;
}

// USD 절대값을 "$X.XB"(10억 달러 단위)로 축약한다.
function formatUsdBillions(value: number): string {
  return `$${(value / 1e9).toFixed(1)}B`;
}

function hsLabel(hs: string): string {
  return HS_NAMES[hs] ?? hs;
}

// -1..+1 센티먼트를 부호 포함 소수 1자리로.
function formatSentiment(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}`;
}

function sentimentClass(value: number): string {
  if (value > 0) {
    return styles.chipPos;
  }
  if (value < 0) {
    return styles.chipNeg;
  }
  return "";
}

function IndexTile({ index }: { index: UsIndex }) {
  const emphasized = index.name === EMPHASIS_INDEX;
  const tileClass = emphasized ? `${styles.tile} ${styles.tileEmphasis}` : styles.tile;
  return (
    <div className={tileClass}>
      <span className={styles.tileName}>{index.name}</span>
      <span className={styles.tileClose}>{index.close}</span>
      <span className={`${styles.tileChange} ${directionClass(index.rising)}`}>
        <span aria-hidden>{directionArrow(index.rising)}</span>
        {signedRatio(index.change_ratio, index.rising)}
      </span>
    </div>
  );
}

export default function MarketOverviewCard() {
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchMarketOverview();
        if (active) {
          setOverview(res);
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "시황 대시보드를 불러오지 못했습니다");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  const dateLabel = formatDate(overview?.market_date ?? null);
  const indices = overview?.us_indices ?? [];
  const hotSectors = overview?.hot_sectors ?? [];
  const tradeSpark = overview?.trade_spark ?? [];

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h2 className={styles.title}>시황 대시보드</h2>
        {dateLabel ? <span className={styles.date}>{dateLabel}</span> : null}
      </div>

      {loading ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : error ? (
        <p className={styles.error}>시황 대시보드 연결 실패: {error}</p>
      ) : (
        <>
          <div className={styles.section}>
            <span className={styles.sectionLabel}>미국 3대 지수</span>
            {indices.length > 0 ? (
              <div className={styles.indices}>
                {indices.map((index) => (
                  <IndexTile key={index.name} index={index} />
                ))}
              </div>
            ) : (
              <p className={styles.empty}>지수 데이터가 없습니다</p>
            )}
          </div>

          <div className={styles.section}>
            <span className={styles.sectionLabel}>핫 섹터</span>
            {hotSectors.length > 0 ? (
              <div className={styles.chips}>
                {hotSectors.map((sector) => (
                  <Link key={sector.sector} href="/industries" className={styles.chip}>
                    <span className={styles.chipName}>{sector.sector}</span>
                    <span className={`${styles.chipSentiment} ${sentimentClass(sector.avg_sentiment)}`}>
                      {formatSentiment(sector.avg_sentiment)}
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <p className={styles.empty}>핫 섹터 데이터가 없습니다</p>
            )}
          </div>

          <div className={styles.section}>
            <span className={styles.sectionLabel}>무역 스파크 · 최신 수출액</span>
            {tradeSpark.length > 0 ? (
              <div className={styles.sparks}>
                {tradeSpark.map((item) => (
                  <div key={item.hs} className={styles.spark}>
                    <span className={styles.sparkName}>{hsLabel(item.hs)}</span>
                    <span className={styles.sparkValue}>{formatUsdBillions(item.export_usd)}</span>
                    <span className={styles.sparkPeriod}>{item.period}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.empty}>무역 데이터가 없습니다</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
