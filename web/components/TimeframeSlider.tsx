"use client";

import type { ChartTimeframe } from "@/lib/types";

import styles from "./TimeframeSlider.module.css";

// 슬라이더 위치(0/1/2) ↔ 기간 매핑. 좌→우로 짧은→긴 기간.
const STEPS: { tf: ChartTimeframe; label: string }[] = [
  { tf: "day", label: "일" },
  { tf: "week", label: "주" },
  { tf: "month", label: "월" },
];

interface Props {
  value: ChartTimeframe;
  onChange: (tf: ChartTimeframe) => void;
  label?: string;
}

// 모든 차트의 기간을 한 번에 조정하는 공용 슬라이드바(일/주/월 3단계).
export default function TimeframeSlider({ value, onChange, label = "기간" }: Props) {
  const index = Math.max(0, STEPS.findIndex((s) => s.tf === value));

  return (
    <div className={styles.wrap}>
      <span className={styles.label}>{label}</span>
      <div className={styles.control}>
        <input
          type="range"
          min={0}
          max={STEPS.length - 1}
          step={1}
          value={index}
          onChange={(e) => onChange(STEPS[Number(e.target.value)].tf)}
          className={styles.range}
          aria-label={`${label} 조정 (일/주/월)`}
          list="tf-ticks"
        />
        <div className={styles.ticks} aria-hidden>
          {STEPS.map((s, i) => (
            <button
              key={s.tf}
              type="button"
              className={i === index ? `${styles.tick} ${styles.tickActive}` : styles.tick}
              onClick={() => onChange(s.tf)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
