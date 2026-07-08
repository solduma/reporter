"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchScreener } from "@/lib/api";
import type { ScreenerMarket, ScreenerOpGrowth, ScreenerResult, ScreenerSort } from "@/lib/types";

import styles from "./page.module.css";

const PAGE_SIZE = 50;
const EOK = 100_000_000; // 1억 = 1e8원

interface Preset<T> {
  label: string;
  value: T;
}

const MKTCAP_MAX_PRESETS: Preset<number>[] = [
  { label: "3천억", value: 300_000_000_000 },
  { label: "5천억", value: 500_000_000_000 },
  { label: "1조", value: 1_000_000_000_000 },
];

// undefined = 하한 없음 → 쿼리 파라미터 생략
const MKTCAP_MIN_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "500억", value: 50_000_000_000 },
  { label: "1000억", value: 100_000_000_000 },
];

const LIQ_PRESETS: Preset<number>[] = [
  { label: "없음", value: 0 },
  { label: "10억", value: 1_000_000_000 },
  { label: "50억", value: 5_000_000_000 },
  { label: "100억", value: 10_000_000_000 },
];

// undefined = 하한 없음 → rev_yoy_min 생략. 값은 비율(0.15 = +15%).
const REV_YOY_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "+15%", value: 0.15 },
  { label: "+30%", value: 0.3 },
  { label: "+50%", value: 0.5 },
];

// undefined = 조건 없음 → op_growth 생략.
const OP_GROWTH_PRESETS: Preset<ScreenerOpGrowth | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "흑자전환", value: "turnaround" },
  { label: "YoY성장", value: "growth" },
];

// 모멘텀 프리셋: 단일 선택 키를 mom_min/mom_max 조합으로 환산한다.
type MomKey = "none" | "up20" | "cut60";

const MOM_PRESETS: Preset<MomKey>[] = [
  { label: "없음", value: "none" },
  { label: "+20%↑", value: "up20" },
  { label: "과열컷(≤60%)", value: "cut60" },
];

function momParams(key: MomKey): { momMin?: number; momMax?: number } {
  switch (key) {
    case "up20":
      return { momMin: 20 };
    case "cut60":
      return { momMax: 60 };
    default:
      return {};
  }
}

const MARKET_PRESETS: Preset<ScreenerMarket | "">[] = [
  { label: "전체", value: "" },
  { label: "KOSDAQ", value: "KOSDAQ" },
  { label: "KOSPI", value: "KOSPI" },
];

// 리포트 커버리지 프리셋: 단일 선택 키를 coverage/recent_buy 쿼리로 환산한다.
type CoverageKey = "none" | "has" | "recent_buy";

const COVERAGE_PRESETS: Preset<CoverageKey>[] = [
  { label: "없음", value: "none" },
  { label: "커버리지있음", value: "has" },
  { label: "최근BUY", value: "recent_buy" },
];

function coverageParams(key: CoverageKey): { coverage?: "has" | "none"; recentBuy?: boolean } {
  switch (key) {
    case "has":
      return { coverage: "has" };
    case "recent_buy":
      return { recentBuy: true };
    default:
      return {};
  }
}

const SORT_PRESETS: Preset<ScreenerSort>[] = [
  { label: "성장스코어", value: "score" },
  { label: "매출성장률", value: "rev_yoy" },
  { label: "모멘텀", value: "momentum" },
  { label: "시총 작은순", value: "market_cap" },
  { label: "거래대금", value: "trading_value" },
  { label: "등락률", value: "change" },
  { label: "리포트순", value: "coverage" },
];

function formatEok(won: number | null): string {
  if (won === null) {
    return "—";
  }
  return `${Math.round(won / EOK).toLocaleString("ko-KR")}억`;
}

function formatPrice(price: number | null): string {
  if (price === null) {
    return "—";
  }
  return price.toLocaleString("ko-KR");
}

function formatPct(pct: number | null): string {
  if (pct === null) {
    return "—";
  }
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

// YoY 비율(0.28)을 반올림 정수 퍼센트("+28%", 대형 성장 "+1,818%")로 표기.
function formatYoy(ratio: number | null): string {
  if (ratio === null) {
    return "—";
  }
  const pct = Math.round(ratio * 100);
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toLocaleString("ko-KR")}%`;
}

// 등락률 색: 한국 관행(상승 빨강/하락 파랑)
function changeClass(pct: number | null): string {
  if (pct === null || pct === 0) {
    return styles.flat;
  }
  return pct > 0 ? styles.up : styles.down;
}

// 성장 지표 색: 개선 초록 / 악화 빨강 (등락률과 별개 관례)
function growthClass(value: number | null): string {
  if (value === null || value === 0) {
    return styles.flat;
  }
  return value > 0 ? styles.gpos : styles.gneg;
}

function scoreNumClass(score: number): string {
  if (score >= 70) {
    return styles.scoreHigh;
  }
  if (score >= 40) {
    return styles.scoreMid;
  }
  return styles.scoreLow;
}

function scoreFillClass(score: number): string {
  if (score >= 70) {
    return styles.scoreFillHigh;
  }
  if (score >= 40) {
    return styles.scoreFillMid;
  }
  return styles.scoreFillLow;
}

export default function ScreenerPage() {
  const router = useRouter();

  // 시장: 성장주 발굴이 목표이므로 KOSDAQ을 기본값으로 둔다("전체"는 ""로 표현).
  const [market, setMarket] = useState<ScreenerMarket | "">("KOSDAQ");
  const [mktcapMax, setMktcapMax] = useState<number>(500_000_000_000);
  const [mktcapMin, setMktcapMin] = useState<number | undefined>(undefined);
  const [liqMin, setLiqMin] = useState<number>(0);
  const [revYoyMin, setRevYoyMin] = useState<number | undefined>(undefined);
  const [opGrowth, setOpGrowth] = useState<ScreenerOpGrowth | undefined>(undefined);
  const [mom, setMom] = useState<MomKey>("none");
  const [coverage, setCoverage] = useState<CoverageKey>("none");
  const [sort, setSort] = useState<ScreenerSort>("score");
  const [offset, setOffset] = useState<number>(0);

  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const { momMin, momMax } = momParams(mom);
        const { coverage: coverageParam, recentBuy } = coverageParams(coverage);
        const res = await fetchScreener({
          mktcapMax,
          mktcapMin,
          liqMin: liqMin > 0 ? liqMin : undefined,
          revYoyMin,
          opGrowth,
          momMin,
          momMax,
          market,
          coverage: coverageParam,
          recentBuy,
          sort,
          limit: PAGE_SIZE,
          offset,
        });
        if (active) {
          setResult(res);
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "스크리너 데이터를 불러오지 못했습니다");
          setResult(null);
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
  }, [market, mktcapMax, mktcapMin, liqMin, revYoyMin, opGrowth, mom, coverage, sort, offset]);

  // 필터 변경 시 첫 페이지로 되돌린다.
  function resetPaging() {
    setOffset(0);
  }

  const total = result?.total ?? 0;
  const items = result?.items ?? [];
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  function renderChips<T>(presets: Preset<T>[], selected: T, onSelect: (value: T) => void) {
    return (
      <div className={styles.chips} role="group">
        {presets.map((preset) => {
          const activeChip = preset.value === selected;
          const classes = [styles.chip];
          if (activeChip) {
            classes.push(styles.chipActive);
          }
          return (
            <button
              key={preset.label}
              type="button"
              aria-pressed={activeChip}
              className={classes.join(" ")}
              onClick={() => {
                onSelect(preset.value);
                resetPaging();
              }}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>스몰캡 성장 스크리너</h1>
        <p className={styles.subtitle}>
          매출·영업이익 성장과 모멘텀으로 소형 성장주 후보를 좁혀보세요 — 톱다운 관점의 1차 스크리닝
        </p>
      </header>

      <section className={styles.filters}>
        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>시가총액 상한</span>
          {renderChips(MKTCAP_MAX_PRESETS, mktcapMax, setMktcapMax)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>시가총액 하한</span>
          {renderChips(MKTCAP_MIN_PRESETS, mktcapMin, setMktcapMin)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>거래대금 최소 (유동성)</span>
          {renderChips(LIQ_PRESETS, liqMin, setLiqMin)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>매출 성장률(YoY) 최소</span>
          {renderChips(REV_YOY_PRESETS, revYoyMin, setRevYoyMin)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>영업이익</span>
          {renderChips(OP_GROWTH_PRESETS, opGrowth, setOpGrowth)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>3개월 모멘텀</span>
          {renderChips(MOM_PRESETS, mom, setMom)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>리포트</span>
          {renderChips(COVERAGE_PRESETS, coverage, setCoverage)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>시장</span>
          {renderChips(MARKET_PRESETS, market, setMarket)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>정렬</span>
          {renderChips(SORT_PRESETS, sort, setSort)}
        </div>
      </section>

      <div className={styles.meta}>
        <span className={styles.total}>
          총 <strong>{total.toLocaleString("ko-KR")}</strong>개 종목
        </span>
        <span className={styles.asOf}>기준일: {result?.as_of ?? "—"}</span>
      </div>

      {error ? (
        <p className={styles.error}>API 연결 실패: {error}</p>
      ) : loading ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className={styles.status}>조건에 맞는 종목이 없습니다</p>
      ) : (
        <>
          <div className={styles.scroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.nameCol} scope="col">
                    종목명
                  </th>
                  <th scope="col">성장스코어</th>
                  <th scope="col">매출YoY</th>
                  <th scope="col">영업이익</th>
                  <th scope="col">모멘텀</th>
                  <th scope="col">시가총액</th>
                  <th scope="col">현재가</th>
                  <th scope="col">등락률</th>
                  <th scope="col">거래대금</th>
                  <th scope="col">리포트</th>
                  <th scope="col">의견</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr
                    key={row.stock_code}
                    className={styles.row}
                    onClick={() => router.push(`/companies/${row.stock_code}`)}
                  >
                    <th className={styles.nameCol} scope="row">
                      <span className={styles.name}>{row.stock_name}</span>
                      <span className={styles.subRow}>
                        <span
                          className={`${styles.badge} ${
                            row.market === "KOSDAQ" ? styles.kosdaq : styles.kospi
                          }`}
                        >
                          {row.market}
                        </span>
                        <span className={styles.code}>{row.stock_code}</span>
                      </span>
                    </th>
                    <td className={styles.scoreCell}>
                      {row.growth_score === null ? (
                        <span className={styles.muted}>—</span>
                      ) : (
                        <div className={styles.score}>
                          <span className={`${styles.scoreNum} ${scoreNumClass(row.growth_score)}`}>
                            {Math.round(row.growth_score)}
                          </span>
                          <span className={styles.scoreBar}>
                            <span
                              className={`${styles.scoreFill} ${scoreFillClass(row.growth_score)}`}
                              style={{ width: `${Math.max(0, Math.min(100, row.growth_score))}%` }}
                            />
                          </span>
                        </div>
                      )}
                    </td>
                    <td className={growthClass(row.revenue_yoy)}>{formatYoy(row.revenue_yoy)}</td>
                    <td>
                      {row.op_turnaround ? (
                        <span className={`${styles.badge} ${styles.turnaround}`}>흑자전환</span>
                      ) : (
                        <span className={growthClass(row.op_yoy)}>{formatYoy(row.op_yoy)}</span>
                      )}
                    </td>
                    <td className={growthClass(row.momentum_3m)}>{formatPct(row.momentum_3m)}</td>
                    <td>{formatEok(row.market_cap)}</td>
                    <td>{formatPrice(row.close_price)}</td>
                    <td className={changeClass(row.change_pct)}>{formatPct(row.change_pct)}</td>
                    <td>{formatEok(row.trading_value)}</td>
                    <td>
                      {row.coverage_count > 0 ? (
                        `${row.coverage_count.toLocaleString("ko-KR")}건`
                      ) : (
                        <span className={styles.muted}>—</span>
                      )}
                    </td>
                    <td>
                      {row.recent_sentiment === null ? (
                        <span className={styles.muted}>—</span>
                      ) : (
                        <span
                          className={`${styles.badge} ${
                            row.recent_sentiment === "BUY" ? styles.senBuy : styles.senHold
                          }`}
                        >
                          {row.recent_sentiment}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className={styles.pagination}>
            <button
              type="button"
              className={styles.pageBtn}
              disabled={!hasPrev}
              onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
            >
              이전
            </button>
            <span className={styles.pageInfo}>
              {currentPage} / {totalPages}
            </span>
            <button
              type="button"
              className={styles.pageBtn}
              disabled={!hasNext}
              onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
            >
              다음
            </button>
          </div>
        </>
      )}
    </div>
  );
}
