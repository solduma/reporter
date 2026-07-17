import type { Peer } from "@/lib/types";

import styles from "./PeersTable.module.css";

interface Props {
  peers: Peer[];
  baseCode: string;
}

// 원시지표 컬럼(네이버 스크랩 + DART 산출 문자열).
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

// 점수 컬럼(테크노펀더멘탈 종합·세부, 0~100). 종목분석과 동일 절대밴드.
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

// 점수 0~100 → 색 등급(종목분석 AnalysisPanel 과 동일 기준: 60↑ 강세 / 40~60 중립 / 40↓ 약세).
function scoreClass(score: number | null): string {
  if (score === null) {
    return styles.scoreNa;
  }
  if (score >= 60) {
    return styles.scoreHigh;
  }
  if (score >= 40) {
    return styles.scoreMid;
  }
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
            {METRIC_COLUMNS.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
              </th>
            ))}
            {SCORE_COLUMNS.map((col) => (
              <th key={col.key} scope="col" className={styles.scoreHead}>
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
                {METRIC_COLUMNS.map((col) => (
                  <td key={col.key}>{peer[col.key] ?? "—"}</td>
                ))}
                {SCORE_COLUMNS.map((col) => {
                  const score = peer[col.key];
                  return (
                    <td key={col.key} className={styles.scoreCell}>
                      <span className={`${styles.score} ${scoreClass(score)}`}>
                        {score === null ? "—" : Math.round(score)}
                      </span>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
