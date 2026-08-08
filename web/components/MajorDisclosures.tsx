"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchMajorDisclosures } from "@/lib/api";
import type { MajorDisclosure } from "@/lib/types";

import styles from "./MajorDisclosures.module.css";

interface Props {
  code: string;
}

const SENTI_LABEL: Record<string, string> = { BUY: "긍정", SELL: "부정", HOLD: "중립" };

// 주요사항보고서(공급계약·수주·유상증자·소송·합병 등) 공시 최신순 표.
// react-query 가 code 별로 캐싱 — 재방문 시 즉시 표시.
export default function MajorDisclosures({ code }: Props) {
  const { data, isPending, error } = useQuery<MajorDisclosure[]>({
    queryKey: ["major-disclosures", code],
    queryFn: () => fetchMajorDisclosures(code),
  });

  if (isPending) {
    return <div className={styles.status}>불러오는 중…</div>;
  }
  if (error) {
    return <div className={styles.error}>주요공시 로드 실패</div>;
  }
  if (!data || data.length === 0) {
    return <div className={styles.status}>주요공시 내역이 없습니다</div>;
  }

  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.thDate}>접수일</th>
            <th className={styles.thTitle}>공시 제목</th>
            <th className={styles.thParty}>제출인</th>
            <th className={styles.thSenti}>판단</th>
            <th className={styles.thLink}>원문</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.rcept_no}>
              <td className={styles.tdDate}>{d.rcept_dt}</td>
              <td className={styles.tdTitle} title={d.rationale}>{d.report_nm}</td>
              <td className={styles.tdParty}>{d.flr_nm || "—"}</td>
              <td className={styles.tdSenti}>
                <span className={sentiClass(d.sentiment)}>{SENTI_LABEL[d.sentiment] ?? d.sentiment}</span>
              </td>
              <td className={styles.tdLink}>
                {d.dart_url ? (
                  <a href={d.dart_url} target="_blank" rel="noopener noreferrer">
                    DART
                  </a>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function sentiClass(s: string): string {
  if (s === "BUY") return `${styles.senti} ${styles.sentiBuy}`;
  if (s === "SELL") return `${styles.senti} ${styles.sentiSell}`;
  return `${styles.senti} ${styles.sentiHold}`;
}