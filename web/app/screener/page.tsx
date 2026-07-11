"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { fetchScreener, fetchScreenerSectors } from "@/lib/api";
import type {
  ScreenerEventKind,
  ScreenerMarket,
  ScreenerOpGrowth,
  ScreenerResult,
  ScreenerSort,
  ScreenerStrategy,
} from "@/lib/types";

import styles from "./page.module.css";

const PAGE_SIZE = 50;
const EOK = 100_000_000; // 1억 = 1e8원

interface Preset<T> {
  label: string;
  value: T;
}

// 전략 탭: 성장/가치/이벤트. 각 탭이 필터·컬럼·스코어를 전환한다.
const STRATEGY_TABS: { value: ScreenerStrategy; label: string; desc: string }[] = [
  { value: "growth", label: "성장", desc: "매출·영업이익 성장과 모멘텀으로 소형 성장주 후보를 좁힌다" },
  { value: "value", label: "가치", desc: "저PER·저PBR·저EV/EBITDA·고ROE 로 저평가 우량주를 찾는다" },
  { value: "event", label: "이벤트", desc: "최근 공시·리포트·급등락·브리핑 이벤트가 발생한 종목을 포착한다" },
];

// undefined = 상한 없음(전체). 값 생략 시 백엔드 default(None)로 전종목.
const MKTCAP_MAX_PRESETS: Preset<number | undefined>[] = [
  { label: "전체", value: undefined },
  { label: "3천억", value: 300_000_000_000 },
  { label: "5천억", value: 500_000_000_000 },
  { label: "1조", value: 1_000_000_000_000 },
];

// 가치 전략 PER 상한
const PER_MAX_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "10배↓", value: 10 },
  { label: "15배↓", value: 15 },
  { label: "20배↓", value: 20 },
];

// 가치 전략 PBR 상한
const PBR_MAX_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "0.5배↓", value: 0.5 },
  { label: "1배↓", value: 1 },
  { label: "1.5배↓", value: 1.5 },
];

// 가치 전략 ROE 하한(%)
const ROE_MIN_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "8%↑", value: 8 },
  { label: "15%↑", value: 15 },
];

// 가치 전략 시가배당률 하한(%)
const DIV_MIN_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "2%↑", value: 2 },
  { label: "3%↑", value: 3 },
  { label: "5%↑", value: 5 },
];

// 이벤트 유형
const EVENT_KIND_PRESETS: Preset<ScreenerEventKind | undefined>[] = [
  { label: "전체", value: undefined },
  { label: "공시", value: "disclosure" },
  { label: "리포트", value: "report" },
  { label: "급등락", value: "surge" },
  { label: "브리핑", value: "broadcast" },
  { label: "뉴스", value: "news" },
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

// 테이블 컬럼 헤더 → 백엔드 sort 키. 매핑 없는 컬럼(종목명·현재가·영업이익)은 정렬 비활성.
interface Column {
  label: string;
  sort?: ScreenerSort;
}

// 전략별 컬럼 세트. 종목명·시총·현재가·등락률·거래대금은 공통, 나머지는 전략 특화.
const COLUMNS_BY_STRATEGY: Record<ScreenerStrategy, Column[]> = {
  growth: [
    { label: "종목명" },
    { label: "성장스코어", sort: "score" },
    { label: "매출YoY", sort: "rev_yoy" },
    { label: "영업이익" },
    { label: "모멘텀", sort: "momentum" },
    { label: "시가총액", sort: "market_cap" },
    { label: "현재가" },
    { label: "등락률", sort: "change" },
    { label: "거래대금", sort: "trading_value" },
    { label: "리포트", sort: "coverage" },
    { label: "의견" },
  ],
  value: [
    { label: "종목명" },
    { label: "가치스코어", sort: "score" },
    { label: "PER" },
    { label: "PBR" },
    { label: "ROE" },
    { label: "배당률" },
    { label: "EV/EBITDA" },
    { label: "시가총액", sort: "market_cap" },
    { label: "현재가" },
    { label: "등락률", sort: "change" },
    { label: "거래대금", sort: "trading_value" },
  ],
  event: [
    { label: "종목명" },
    { label: "이벤트" },
    { label: "요약" },
    { label: "발생일" },
    { label: "현재가" },
    { label: "등락률", sort: "change" },
    { label: "거래대금", sort: "trading_value" },
    { label: "시가총액", sort: "market_cap" },
  ],
};

// 백엔드 단방향 정렬: 시총만 오름차순, 나머지는 내림차순. 방향 표시(▲/▼)에 사용.
function sortArrow(sort: ScreenerSort): string {
  return sort === "market_cap" ? "▲" : "▼";
}

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

// PER/PBR/EV 배수 표기(소수 1~2자리). 결측·비양수는 —.
function formatMultiple(v: number | null): string {
  if (v === null || v <= 0) {
    return "—";
  }
  return `${v.toFixed(v < 10 ? 2 : 1)}배`;
}

// ROE·배당률 퍼센트(%) 표기.
function formatRoe(v: number | null): string {
  if (v === null) {
    return "—";
  }
  return `${v.toFixed(1)}%`;
}

// 시가배당률(%) — 0 또는 결측이면 —.
function formatDiv(v: number | null): string {
  if (v === null || v <= 0) {
    return "—";
  }
  return `${v.toFixed(2)}%`;
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

function ScreenerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // 산업 흐름 페이지에서 ?sector=<섹터명>으로 넘어오면 초기 섹터 필터로 적용.
  // URLSearchParams.get()은 퍼센트 인코딩을 자동 디코딩한다.
  const [sector, setSector] = useState<string>(() => searchParams.get("sector") ?? "");
  const [sectors, setSectors] = useState<string[]>([]);

  const [strategy, setStrategy] = useState<ScreenerStrategy>("growth");
  // 시장: 성장주 발굴이 목표이므로 KOSDAQ을 기본값으로 둔다("전체"는 ""로 표현).
  const [market, setMarket] = useState<ScreenerMarket | "">("KOSDAQ");
  const [mktcapMax, setMktcapMax] = useState<number | undefined>(500_000_000_000);
  const [mktcapMin, setMktcapMin] = useState<number | undefined>(undefined);
  const [liqMin, setLiqMin] = useState<number>(0);
  const [revYoyMin, setRevYoyMin] = useState<number | undefined>(undefined);
  const [opGrowth, setOpGrowth] = useState<ScreenerOpGrowth | undefined>(undefined);
  const [mom, setMom] = useState<MomKey>("none");
  const [coverage, setCoverage] = useState<CoverageKey>("none");
  // 가치 전략 필터
  const [perMax, setPerMax] = useState<number | undefined>(undefined);
  const [pbrMax, setPbrMax] = useState<number | undefined>(undefined);
  const [roeMin, setRoeMin] = useState<number | undefined>(undefined);
  const [divMin, setDivMin] = useState<number | undefined>(undefined);
  // 이벤트 전략 필터
  const [eventKind, setEventKind] = useState<ScreenerEventKind | undefined>(undefined);
  const [sort, setSort] = useState<ScreenerSort>("score");
  const [offset, setOffset] = useState<number>(0);

  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchScreenerSectors()
      .then((list) => {
        if (active) {
          setSectors(list);
        }
      })
      .catch(() => {
        // 섹터 목록 로드 실패는 무시 — 스크리너 본체는 계속 동작.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const { momMin, momMax } = momParams(mom);
        const { coverage: coverageParam, recentBuy } = coverageParams(coverage);
        const res = await fetchScreener({
          strategy,
          mktcapMax,
          mktcapMin,
          liqMin: liqMin > 0 ? liqMin : undefined,
          revYoyMin,
          opGrowth,
          momMin,
          momMax,
          perMax,
          pbrMax,
          roeMin,
          divMin,
          eventKind,
          market,
          sector: sector || undefined,
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
  }, [strategy, market, sector, mktcapMax, mktcapMin, liqMin, revYoyMin, opGrowth, mom, coverage, perMax, pbrMax, roeMin, divMin, eventKind, sort, offset]);

  // 필터 변경 시 첫 페이지로 되돌린다.
  function resetPaging() {
    setOffset(0);
  }

  // 전략 전환: 정렬·페이지 리셋 + 전략 전용 필터 초기화. 안 그러면 숨겨진 필터가 다른 전략에
  // 새어 들어간다(예: 성장 탭의 rev_yoy_min 이 가치 결과를 걸러버림).
  function changeStrategy(next: ScreenerStrategy) {
    setStrategy(next);
    setSort("score");
    setOffset(0);
    // 성장 전용
    setRevYoyMin(undefined);
    setOpGrowth(undefined);
    setMom("none");
    setCoverage("none");
    // 가치 전용
    setPerMax(undefined);
    setPbrMax(undefined);
    setRoeMin(undefined);
    setDivMin(undefined);
    // 이벤트 전용
    setEventKind(undefined);
  }

  const columns = COLUMNS_BY_STRATEGY[strategy];
  const strategyDesc = STRATEGY_TABS.find((t) => t.value === strategy)?.desc ?? "";

  // 스코어 셀(성장·가치 공용): 숫자 + 컬러 바.
  function renderScoreCell(score: number | null) {
    if (score === null) {
      return (
        <td className={styles.scoreCell}>
          <span className={styles.muted}>—</span>
        </td>
      );
    }
    return (
      <td className={styles.scoreCell}>
        <div className={styles.score}>
          <span className={`${styles.scoreNum} ${scoreNumClass(score)}`}>{Math.round(score)}</span>
          <span className={styles.scoreBar}>
            <span
              className={`${styles.scoreFill} ${scoreFillClass(score)}`}
              style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
            />
          </span>
        </div>
      </td>
    );
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
        <h1 className={styles.title}>종목 스크리너</h1>
        <p className={styles.subtitle}>{strategyDesc}</p>
        <div className={styles.strategyTabs} role="tablist" aria-label="스크리너 전략">
          {STRATEGY_TABS.map((tab) => {
            const on = tab.value === strategy;
            return (
              <button
                key={tab.value}
                type="button"
                role="tab"
                aria-selected={on}
                className={on ? `${styles.strategyTab} ${styles.strategyTabActive}` : styles.strategyTab}
                onClick={() => changeStrategy(tab.value)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
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

        {/* 성장 전략 전용 필터 */}
        {strategy === "growth" ? (
          <>
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
          </>
        ) : null}

        {/* 가치 전략 전용 필터 */}
        {strategy === "value" ? (
          <>
            <div className={styles.filterGroup}>
              <span className={styles.filterLabel}>PER 상한</span>
              {renderChips(PER_MAX_PRESETS, perMax, setPerMax)}
            </div>
            <div className={styles.filterGroup}>
              <span className={styles.filterLabel}>PBR 상한</span>
              {renderChips(PBR_MAX_PRESETS, pbrMax, setPbrMax)}
            </div>
            <div className={styles.filterGroup}>
              <span className={styles.filterLabel}>ROE 하한</span>
              {renderChips(ROE_MIN_PRESETS, roeMin, setRoeMin)}
            </div>
            <div className={styles.filterGroup}>
              <span className={styles.filterLabel}>시가배당률 하한</span>
              {renderChips(DIV_MIN_PRESETS, divMin, setDivMin)}
            </div>
          </>
        ) : null}

        {/* 이벤트 전략 전용 필터 */}
        {strategy === "event" ? (
          <div className={styles.filterGroup}>
            <span className={styles.filterLabel}>이벤트 유형</span>
            {renderChips(EVENT_KIND_PRESETS, eventKind, setEventKind)}
          </div>
        ) : null}

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>시장</span>
          {renderChips(MARKET_PRESETS, market, setMarket)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>섹터</span>
          {renderChips(
            [{ label: "전체", value: "" }, ...sectors.map((s) => ({ label: s, value: s }))],
            sector,
            setSector,
          )}
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
                  {columns.map((col, index) => {
                    const sortable = col.sort !== undefined;
                    const activeSort = sortable && col.sort === sort;
                    const classes = [];
                    if (index === 0) {
                      classes.push(styles.nameCol);
                    }
                    if (sortable) {
                      classes.push(styles.sortable);
                    }
                    if (activeSort) {
                      classes.push(styles.sortActive);
                    }
                    return (
                      <th
                        key={col.label}
                        scope="col"
                        className={classes.join(" ")}
                        aria-sort={activeSort ? "descending" : undefined}
                        onClick={
                          sortable
                            ? () => {
                                setSort(col.sort as ScreenerSort);
                                resetPaging();
                              }
                            : undefined
                        }
                      >
                        {col.label}
                        {activeSort ? (
                          <span className={styles.sortArrow}> {sortArrow(col.sort as ScreenerSort)}</span>
                        ) : null}
                      </th>
                    );
                  })}
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

                    {strategy === "growth" ? (
                      <>
                        {renderScoreCell(row.score)}
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
                      </>
                    ) : null}

                    {strategy === "value" ? (
                      <>
                        {renderScoreCell(row.score)}
                        <td>{formatMultiple(row.per)}</td>
                        <td>{formatMultiple(row.pbr)}</td>
                        <td>{formatRoe(row.roe)}</td>
                        <td className={row.div_yield && row.div_yield > 0 ? styles.gpos : undefined}>
                          {formatDiv(row.div_yield)}
                        </td>
                        <td>{formatMultiple(row.ev_ebitda)}</td>
                        <td>{formatEok(row.market_cap)}</td>
                        <td>{formatPrice(row.close_price)}</td>
                        <td className={changeClass(row.change_pct)}>{formatPct(row.change_pct)}</td>
                        <td>{formatEok(row.trading_value)}</td>
                      </>
                    ) : null}

                    {strategy === "event" ? (
                      <>
                        <td>
                          {row.event_kind ? (
                            <span className={`${styles.badge} ${styles.eventBadge}`}>{row.event_kind}</span>
                          ) : (
                            <span className={styles.muted}>—</span>
                          )}
                        </td>
                        <td className={styles.eventSummary}>{row.event_summary ?? "—"}</td>
                        <td>{row.event_date ?? "—"}</td>
                        <td>{formatPrice(row.close_price)}</td>
                        <td className={changeClass(row.change_pct)}>{formatPct(row.change_pct)}</td>
                        <td>{formatEok(row.trading_value)}</td>
                        <td>{formatEok(row.market_cap)}</td>
                      </>
                    ) : null}
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

export default function ScreenerPage() {
  return (
    <Suspense fallback={<p className={styles.status}>불러오는 중…</p>}>
      <ScreenerContent />
    </Suspense>
  );
}
