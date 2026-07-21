"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import InfoDot from "@/components/InfoDot";
import StockSearch from "@/components/StockSearch";
import { fetchScreener, fetchScreenerSectors } from "@/lib/api";
import { GLOSSARY } from "@/lib/glossary";
import { useAutoTour } from "@/lib/useAutoTour";
import { usePersistentState } from "@/lib/usePersistentState";
import type {
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

// 전략 탭: 종합/성장/가치/추세/탑다운(테크노펀더멘탈 4축 + 종합). 각 탭이 컬럼·스코어를 전환한다.
const STRATEGY_TABS: { value: ScreenerStrategy; label: string; desc: string }[] = [
  { value: "overall", label: "종합", desc: "성장·가치·추세·탑다운을 종합한 테크노펀더멘탈 점수로 정렬한다" },
  { value: "growth", label: "성장", desc: "매출·영업이익 성장과 모멘텀으로 성장주 후보를 좁힌다" },
  { value: "value", label: "가치", desc: "저PER·저PBR·저EV/EBITDA·고ROE 로 저평가 우량주를 찾는다" },
  { value: "trend", label: "추세", desc: "신고가 근접·이평 정배열·거래량·수익률 종합 기술적 추세로 정렬한다" },
  { value: "topdown", label: "탑다운", desc: "종목이 속한 섹터의 국내·미국 수급(자금유입)으로 정렬한다" },
];

// undefined = 상한 없음(전체). 값 생략 시 백엔드 default(None)로 전종목.
const MKTCAP_MAX_PRESETS: Preset<number | undefined>[] = [
  { label: "전체", value: undefined },
  { label: "3천억", value: 300_000_000_000 },
  { label: "5천억", value: 500_000_000_000 },
  { label: "1조", value: 1_000_000_000_000 },
  { label: "2조", value: 2_000_000_000_000 },
  { label: "5조", value: 5_000_000_000_000 },
  { label: "10조", value: 10_000_000_000_000 },
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

// undefined = 하한 없음 → 쿼리 파라미터 생략. 스몰캡~중형캡까지 커버.
const MKTCAP_MIN_PRESETS: Preset<number | undefined>[] = [
  { label: "없음", value: undefined },
  { label: "500억", value: 50_000_000_000 },
  { label: "1000억", value: 100_000_000_000 },
  { label: "3000억", value: 300_000_000_000 },
  { label: "5000억", value: 500_000_000_000 },
  { label: "1조", value: 1_000_000_000_000 },
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
  info?: keyof typeof GLOSSARY; // 초보자 툴팁(용어사전 키)
}

// 공통 시세 컬럼(시총·현재가·등락률·거래대금)과 이벤트 컬럼은 모든 전략에서 끝에 붙는다.
const TAIL_COLUMNS: Column[] = [
  { label: "시가총액", sort: "market_cap" },
  { label: "현재가" },
  { label: "등락률", sort: "change" },
  { label: "거래대금", sort: "trading_value" },
  { label: "이벤트" }, // 최근 공시·리포트·브리핑·뉴스 (별도 탭 제거 → 컬럼으로)
];

// 전략별 컬럼 세트 = 종목명 + 전략 특화 + 공통 꼬리. 스코어 컬럼은 전략별로 이름만 다르다.
const COLUMNS_BY_STRATEGY: Record<ScreenerStrategy, Column[]> = {
  overall: [
    { label: "종목명" },
    { label: "종합스코어", sort: "score", info: "score" },
    { label: "성장", info: "score" },
    { label: "가치", info: "score" },
    { label: "추세", info: "rs_rating" },
    { label: "탑다운" },
    ...TAIL_COLUMNS,
  ],
  growth: [
    { label: "종목명" },
    { label: "성장스코어", sort: "score", info: "score" },
    { label: "매출YoY", sort: "rev_yoy", info: "revenue_yoy" },
    { label: "영업이익", info: "op_yoy" },
    { label: "모멘텀", sort: "momentum", info: "momentum" },
    { label: "RS", info: "rs_rating" },
    { label: "리포트", sort: "coverage", info: "coverage" },
    { label: "의견" },
    ...TAIL_COLUMNS,
  ],
  value: [
    { label: "종목명" },
    { label: "가치스코어", sort: "score", info: "score" },
    { label: "PER", info: "per" },
    { label: "PBR", info: "pbr" },
    { label: "ROE", info: "roe" },
    { label: "배당률" },
    { label: "EV/EBITDA", info: "ev_ebitda" },
    ...TAIL_COLUMNS,
  ],
  trend: [
    { label: "종목명" },
    { label: "추세스코어", sort: "score", info: "score" },
    { label: "RS", info: "rs_rating" },
    { label: "모멘텀", sort: "momentum", info: "momentum" },
    ...TAIL_COLUMNS,
  ],
  topdown: [
    { label: "종목명" },
    { label: "탑다운스코어", sort: "score", info: "score" },
    { label: "섹터" },
    ...TAIL_COLUMNS,
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

// RS Rating 색: 80↑ 주도주(초록) / 40↓ 약함(빨강) / 그 사이 중립.
function rsRatingClass(rating: number | null): string {
  if (rating === null) {
    return styles.flat;
  }
  if (rating >= 80) {
    return styles.gpos;
  }
  return rating < 40 ? styles.gneg : styles.flat;
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

  // 필터 선택은 프리셋으로 강제하지 않고 localStorage 에 저장해 다음 방문에도 유지한다(기본값은
  // 저장값이 없을 때만 적용). 페이지(offset)·UI 토글(filtersOpen)·런타임(result 등)은 저장 제외.
  const [strategy, setStrategy] = usePersistentState<ScreenerStrategy>("screener.strategy", "overall");
  const [filtersOpen, setFiltersOpen] = useState<boolean>(false);
  // 시장: 성장주 발굴이 목표이므로 KOSDAQ을 기본값으로 둔다("전체"는 ""로 표현).
  const [market, setMarket] = usePersistentState<ScreenerMarket | "">("screener.market", "");
  const [mktcapMax, setMktcapMax] = usePersistentState<number | undefined>("screener.mktcapMax", undefined);
  const [mktcapMin, setMktcapMin] = usePersistentState<number | undefined>("screener.mktcapMin", undefined);
  const [liqMin, setLiqMin] = usePersistentState<number>("screener.liqMin", 0);
  const [revYoyMin, setRevYoyMin] = usePersistentState<number | undefined>("screener.revYoyMin", undefined);
  const [opGrowth, setOpGrowth] = usePersistentState<ScreenerOpGrowth | undefined>("screener.opGrowth", undefined);
  const [mom, setMom] = usePersistentState<MomKey>("screener.mom", "none");
  const [coverage, setCoverage] = usePersistentState<CoverageKey>("screener.coverage", "none");
  // 가치 전략 필터
  const [perMax, setPerMax] = usePersistentState<number | undefined>("screener.perMax", undefined);
  const [pbrMax, setPbrMax] = usePersistentState<number | undefined>("screener.pbrMax", undefined);
  const [roeMin, setRoeMin] = usePersistentState<number | undefined>("screener.roeMin", undefined);
  const [divMin, setDivMin] = usePersistentState<number | undefined>("screener.divMin", undefined);
  const [sort, setSort] = usePersistentState<ScreenerSort>("screener.sort", "score");
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
  }, [strategy, market, sector, mktcapMax, mktcapMin, liqMin, revYoyMin, opGrowth, mom, coverage, perMax, pbrMax, roeMin, divMin, sort, offset]);

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
  }

  const columns = COLUMNS_BY_STRATEGY[strategy];
  const strategyDesc = STRATEGY_TABS.find((t) => t.value === strategy)?.desc ?? "";

  // 기본값과 다른(=사용자가 건드린) 필터 개수 — 접힌 상태에서도 몇 개 걸렸는지 배지로 보인다.
  const activeFilterCount = [
    mktcapMax !== 500_000_000_000,
    mktcapMin !== undefined,
    liqMin > 0,
    market !== "KOSDAQ",
    sector !== "",
    revYoyMin !== undefined,
    opGrowth !== undefined,
    mom !== "none",
    coverage !== "none",
    perMax !== undefined,
    pbrMax !== undefined,
    roeMin !== undefined,
    divMin !== undefined,
  ].filter(Boolean).length;

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

  // 종합 탭의 축별 미니 점수 셀(숫자만, 색 등급). null 은 —.
  function renderMiniScore(score: number | null) {
    if (score === null) {
      return (
        <td className={styles.miniScoreCell}>
          <span className={styles.muted}>—</span>
        </td>
      );
    }
    return (
      <td className={styles.miniScoreCell}>
        <span className={`${styles.miniScore} ${scoreNumClass(score)}`}>{Math.round(score)}</span>
      </td>
    );
  }

  const total = result?.total ?? 0;
  const items = result?.items ?? [];
  // 결과가 뜬 뒤(요소 존재) 첫 방문 1회 온보딩 투어 자동 시작.
  useAutoTour("screener", !loading && items.length > 0);
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
        <h1 className={styles.title}>국내 스크리너</h1>
        <p className={styles.subtitle}>{strategyDesc}</p>
        <StockSearch />
        <div className={styles.strategyTabs} role="tablist" aria-label="스크리너 전략" data-tour="strategy">
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

      <section className={styles.filters} data-tour="filters">
        <button
          type="button"
          className={styles.filterToggle}
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((v) => !v)}
        >
          <span className={styles.filterToggleLabel}>
            필터
            {activeFilterCount > 0 ? (
              <span className={styles.filterCount}>{activeFilterCount}</span>
            ) : null}
          </span>
          <span className={styles.filterToggleHint}>
            {filtersOpen ? "접기 ▲" : "펼치기 ▼"}
          </span>
        </button>
        {filtersOpen ? (
        <div className={styles.filterBody}>
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
        </div>
        ) : null}
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
            <table className={styles.table} data-tour="results">
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
                        {col.info ? <InfoDot termKey={col.info} /> : null}
                        {activeSort ? (
                          <span className={styles.sortArrow}> {sortArrow(col.sort as ScreenerSort)}</span>
                        ) : null}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {items.map((row, rowIndex) => (
                  <tr
                    key={row.stock_code}
                    className={styles.row}
                    data-tour={rowIndex === 0 ? "firstRow" : undefined}
                    onClick={() => router.push(`/companies/${row.stock_code}`)}
                  >
                    <th className={styles.nameCol} scope="row">
                      <span className={styles.name}>
                        {row.stock_name}
                      </span>
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

                    {strategy === "overall" ? (
                      <>
                        {renderScoreCell(row.score)}
                        {renderMiniScore(row.growth_score)}
                        {renderMiniScore(row.value_score)}
                        {renderMiniScore(row.trend_score)}
                        {renderMiniScore(row.topdown_score)}
                      </>
                    ) : null}

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
                        <td className={rsRatingClass(row.rs_rating)}>{row.rs_rating ?? "—"}</td>
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
                      </>
                    ) : null}

                    {strategy === "trend" ? (
                      <>
                        {renderScoreCell(row.score)}
                        <td className={rsRatingClass(row.rs_rating)}>{row.rs_rating ?? "—"}</td>
                        <td className={growthClass(row.momentum_3m)}>{formatPct(row.momentum_3m)}</td>
                      </>
                    ) : null}

                    {strategy === "topdown" ? (
                      <>
                        {renderScoreCell(row.score)}
                        <td className={styles.sectorCell}>{row.kr_sector ?? "—"}</td>
                      </>
                    ) : null}

                    {/* 공통 꼬리: 시총·현재가·등락률·거래대금·이벤트 */}
                    <td>{formatEok(row.market_cap)}</td>
                    <td>{formatPrice(row.close_price)}</td>
                    <td className={changeClass(row.change_pct)}>{formatPct(row.change_pct)}</td>
                    <td>{formatEok(row.trading_value)}</td>
                    <td className={styles.eventCell}>
                      {row.event_kind ? (
                        <span
                          className={styles.eventWrap}
                          title={
                            row.event_summary
                              ? `${row.event_summary}${row.event_date ? ` (${row.event_date})` : ""}`
                              : undefined
                          }
                        >
                          <span className={`${styles.badge} ${styles.eventBadge}`}>
                            {row.event_kind}
                          </span>
                          {row.event_summary ? (
                            <span className={styles.eventSummary}>{row.event_summary}</span>
                          ) : null}
                        </span>
                      ) : (
                        <span className={styles.muted}>—</span>
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

export default function ScreenerPage() {
  return (
    <Suspense fallback={<p className={styles.status}>불러오는 중…</p>}>
      <ScreenerContent />
    </Suspense>
  );
}
