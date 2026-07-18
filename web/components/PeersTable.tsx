import type { Peer } from "@/lib/types";

import styles from "./PeersTable.module.css";

interface Props {
  peers: Peer[];
  baseCode: string;
}

interface MetricColumn {
  key: keyof Pick<
    Peer,
    "price" | "market_cap" | "foreign_ratio" | "per" | "pbr" | "psr" | "roe" | "ev_ebitda"
  >;
  label: string;
}

const METRIC_COLUMNS: MetricColumn[] = [
  { key: "price", label: "현재가" },
  { key: "market_cap", label: "시가총액" },
  { key: "foreign_ratio", label: "외국인비율" },
  { key: "per", label: "PER" },
  { key: "pbr", label: "PBR" },
  { key: "psr", label: "PSR" },
  { key: "roe", label: "ROE" },
  { key: "ev_ebitda", label: "EV/EBITDA" },
];

interface ScoreColumn {
  key: keyof Pick<
    Peer,
    "overall_score" | "growth_score" | "value_score" | "trend_score" | "topdown_score"
  >;
  label: string;
}

const SCORE_COLUMNS: ScoreColumn[] = [
  { key: "overall_score", label: "종합" },
  { key: "growth_score", label: "성장" },
  { key: "value_score", label: "가치" },
  { key: "trend_score", label: "추세" },
  { key: "topdown_score", label: "탑다운" },
];

// 종목분석 AnalysisPanel 과 동일 색 기준(60↑ 매수·40~60 중립·40↓ 매도).
function scoreClass(score: number | null): string {
  if (score === null) return styles.scoreNa;
  if (score >= 60) return styles.scoreHigh;
  if (score >= 40) return styles.scoreMid;
  return styles.scoreLow;
}

export default function PeersTable({ peers, baseCode }: Props) {
  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.nameCol} scope="col">
              종목
            </th>
            {SCORE_COLUMNS.map((col, i) => (
              <th
                key={col.key}
                scope="col"
                className={i === SCORE_COLUMNS.length - 1 ? styles.scoreHead : undefined}
              >
                {col.label}
              </th>
            ))}
            {METRIC_COLUMNS.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {peers.map((peer) => {
            const isBase = peer.stock_code === baseCode;
            return (
              <tr key={peer.stock_code} className={isBase ? styles.base : undefined}>
                <th className={styles.nameCol} scope="row">
                  <span className={styles.name}>{peer.name}</span>
                  <span className={styles.code}>{peer.stock_code}</span>
                </th>
                {SCORE_COLUMNS.map((col, i) => {
                  const v = peer[col.key];
                  // 점수 묶음을 앞에 두고, 마지막 점수 컬럼에 우측 경계선으로 원시지표와 구분.
                  const border = i === SCORE_COLUMNS.length - 1 ? styles.scoreHead : "";
                  return (
                    <td key={col.key} className={`${border} ${styles.score} ${scoreClass(v)}`}>
                      {v === null || v === undefined ? "—" : Math.round(v)}
                    </td>
                  );
                })}
                {METRIC_COLUMNS.map((col) => (
                  <td key={col.key}>{peer[col.key] ?? "—"}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
