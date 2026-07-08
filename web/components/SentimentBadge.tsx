import type { Sentiment } from "@/lib/types";

import styles from "./SentimentBadge.module.css";

const LABELS: Record<Sentiment, string> = {
  BUY: "매수",
  SELL: "매도",
  HOLD: "중립",
};

const CLASS: Record<Sentiment, string> = {
  BUY: styles.buy,
  SELL: styles.sell,
  HOLD: styles.hold,
};

export default function SentimentBadge({ sentiment }: { sentiment: Sentiment }) {
  return (
    <span className={`${styles.badge} ${CLASS[sentiment] ?? styles.hold}`}>
      {sentiment} · {LABELS[sentiment] ?? sentiment}
    </span>
  );
}
