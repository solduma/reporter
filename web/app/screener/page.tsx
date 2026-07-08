"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchScreener } from "@/lib/api";
import type { ScreenerMarket, ScreenerResult, ScreenerSort } from "@/lib/types";

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

const MARKET_PRESETS: Preset<ScreenerMarket | "">[] = [
  { label: "전체", value: "" },
  { label: "KOSDAQ", value: "KOSDAQ" },
  { label: "KOSPI", value: "KOSPI" },
];

interface SortPreset extends Preset<ScreenerSort> {
  // 3개월 수익률 데이터가 아직 없어 모멘텀 정렬은 비활성화한다.
  disabled?: boolean;
}

const SORT_PRESETS: SortPreset[] = [
  { label: "시총 작은순", value: "market_cap" },
  { label: "거래대금", value: "trading_value" },
  { label: "등락률", value: "change" },
  { label: "모멘텀(준비중)", value: "momentum", disabled: true },
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

// 3개월 수익률(모멘텀). 데이터 소스 미비로 현재는 항상 null → "—".
function formatMomentum(rate: number | null): string {
  return formatPct(rate);
}

function changeClass(pct: number | null): string {
  if (pct === null || pct === 0) {
    return styles.flat;
  }
  return pct > 0 ? styles.up : styles.down;
}

export default function ScreenerPage() {
  const router = useRouter();

  // 시장: 성장주 발굴이 목표이므로 KOSDAQ을 기본값으로 둔다("전체"는 ""로 표현).
  const [market, setMarket] = useState<ScreenerMarket | "">("KOSDAQ");
  const [mktcapMax, setMktcapMax] = useState<number>(500_000_000_000);
  const [mktcapMin, setMktcapMin] = useState<number | undefined>(undefined);
  const [liqMin, setLiqMin] = useState<number>(0);
  const [sort, setSort] = useState<ScreenerSort>("market_cap");
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
        const res = await fetchScreener({
          mktcapMax,
          mktcapMin,
          liqMin: liqMin > 0 ? liqMin : undefined,
          market,
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
  }, [market, mktcapMax, mktcapMin, liqMin, sort, offset]);

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

  function renderChips<T>(
    presets: Preset<T>[],
    selected: T,
    onSelect: (value: T) => void,
    isDisabled?: (value: T) => boolean,
  ) {
    return (
      <div className={styles.chips} role="group">
        {presets.map((preset) => {
          const disabled = isDisabled?.(preset.value) ?? false;
          const activeChip = preset.value === selected;
          const classes = [styles.chip];
          if (activeChip) {
            classes.push(styles.chipActive);
          }
          return (
            <button
              key={preset.label}
              type="button"
              disabled={disabled}
              aria-pressed={activeChip}
              className={classes.join(" ")}
              onClick={() => {
                if (!disabled) {
                  onSelect(preset.value);
                  resetPaging();
                }
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
          시가총액 상한과 유동성으로 소형 성장주 후보를 좁혀보세요 — 톱다운 관점의 1차 스크리닝
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
          <span className={styles.filterLabel}>시장</span>
          {renderChips(MARKET_PRESETS, market, setMarket)}
        </div>

        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>정렬</span>
          {renderChips(SORT_PRESETS, sort, setSort, (value) =>
            Boolean(SORT_PRESETS.find((p) => p.value === value)?.disabled),
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
                  <th className={styles.nameCol} scope="col">
                    종목명
                  </th>
                  <th scope="col">시가총액</th>
                  <th scope="col">현재가</th>
                  <th scope="col">등락률</th>
                  <th scope="col">거래대금</th>
                  <th scope="col">3개월 수익률</th>
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
                    <td>{formatEok(row.market_cap)}</td>
                    <td>{formatPrice(row.close_price)}</td>
                    <td className={changeClass(row.change_pct)}>{formatPct(row.change_pct)}</td>
                    <td>{formatEok(row.trading_value)}</td>
                    <td className={styles.muted}>{formatMomentum(row.three_month_rate)}</td>
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
