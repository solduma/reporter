import type { MarketBrief } from "@/lib/types";

import styles from "./MarketBriefCard.module.css";

interface Props {
  brief: MarketBrief | null;
}

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

export default function MarketBriefCard({ brief }: Props) {
  const summary = brief?.summary?.trim() ?? "";
  const dateLabel = formatDate(brief?.market_date ?? null);

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h1 className={styles.title}>오늘의 시황</h1>
        {dateLabel ? <span className={styles.date}>{dateLabel}</span> : null}
      </div>
      {summary ? (
        <p className={styles.summary}>{summary}</p>
      ) : (
        <p className={styles.empty}>오늘의 시황 데이터가 아직 없습니다</p>
      )}
    </section>
  );
}
