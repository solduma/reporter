"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchSectorFlow } from "@/lib/api";
import type { FlowMarket, LookbackPeriod, SectorFlowRow } from "@/lib/types";

import styles from "./SectorFlowTable.module.css";

const LOOKBACK_LABELS: Record<LookbackPeriod, string> = {
  "1d": "당일",
  "1w": "이번 주",
  "1m": "이번 달",
  "3m": "3개월",
  "1y": "1년",
};

const LOOKBACK_OPTIONS: LookbackPeriod[] = ["1d", "1w", "1m", "3m", "1y"];

type State = {
  status: "loading" | "ready" | "error";
  rows: SectorFlowRow[];
  message?: string;
};

function scoreClass(score: number | null): string {
  if (score === null) {
    return styles.flat;
  }
  if (score >= 60) {
    return styles.hot;
  }
  if (score >= 40) {
    return styles.warm;
  }
  return styles.cool;
}

function pct(v: number | null): string {
  if (v === null) {
    return "—";
  }
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function delta(v: number | null): string {
  if (v === null) {
    return "—";
  }
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}pp`;
}

export default function SectorFlowTable() {
  const [market, setMarket] = useState<FlowMarket>("KR");
  const [lookback, setLookback] = useState<LookbackPeriod>("3m");
  const [state, setState] = useState<State>({ status: "loading", rows: [] });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", rows: [] });
      try {
        const rows = await fetchSectorFlow(market, lookback);
        if (active) {
          setState({ status: "ready", rows });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            rows: [],
            message: e instanceof Error ? e.message : "수급 섹터를 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [market, lookback]);

  return (
    <div className={styles.wrap}>
      <div className={styles.tabs} role="tablist" aria-label="시장 선택">
        {(["KR", "US"] as FlowMarket[]).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={m === market}
            className={m === market ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            onClick={() => setMarket(m)}
          >
            {m === "KR" ? "국내" : "미국"}
          </button>
        ))}
        <span className={styles.lookbackGroup}>
          {LOOKBACK_OPTIONS.map((lb) => (
            <button
              key={lb}
              type="button"
              className={lb === lookback ? `${styles.lbTab} ${styles.lbActive}` : styles.lbTab}
              onClick={() => setLookback(lb)}
            >
              {LOOKBACK_LABELS[lb]}
            </button>
          ))}
        </span>
      </div>

      {state.status === "loading" ? (
        <p className={styles.status}>수급 계산 중…</p>
      ) : state.status === "error" ? (
        <p className={styles.error}>API 연결 실패: {state.message}</p>
      ) : state.rows.length === 0 ? (
        <p className={styles.status}>수급 섹터 데이터가 없습니다</p>
      ) : (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.rankCol}>순위</th>
                <th className={styles.sectorCol}>섹터</th>
                <th>자금유입</th>
                <th>{LOOKBACK_LABELS[lookback]}</th>
                <th>고점근접</th>
                <th>거래량</th>
                {market === "KR" ? <th>외국인Δ</th> : null}
                <th className={styles.linkCol} aria-label="국내 스크리너" />
              </tr>
            </thead>
            <tbody>
              {state.rows.map((r, i) => (
                <tr key={r.symbol}>
                  <td className={styles.rankCol}>
                    <span className={i < 3 ? styles.rankTop : styles.rank}>{i + 1}</span>
                  </td>
                  <th className={styles.sectorCol}>
                    <Link
                      href={`/industries/${encodeURIComponent(r.sector)}`}
                      className={styles.sectorLink}
                    >
                      {r.sector}
                    </Link>
                  </th>
                  <td>
                    <span className={`${styles.score} ${scoreClass(r.flow_score)}`}>
                      {r.flow_score === null ? "—" : Math.round(r.flow_score)}
                    </span>
                  </td>
                  <td className={r.return_3m && r.return_3m > 0 ? styles.up : styles.down}>
                    {pct(r.return_3m)}
                  </td>
                  <td>{r.near_high_pct === null ? "—" : `${r.near_high_pct.toFixed(0)}%`}</td>
                  <td>{r.vol_ratio === null ? "—" : `${r.vol_ratio.toFixed(2)}x`}</td>
                  {market === "KR" ? (
                    <td className={r.foreign_delta && r.foreign_delta > 0 ? styles.up : styles.down}>
                      {delta(r.foreign_delta)}
                    </td>
                  ) : null}
                  <td className={styles.linkCol}>
                    <Link
                      href={`/screener?sector=${encodeURIComponent(r.sector)}`}
                      className={styles.sectorLink}
                    >
                      이 섹터 종목 →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
