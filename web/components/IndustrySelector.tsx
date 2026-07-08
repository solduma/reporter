"use client";

import type { Industry } from "@/lib/types";

import styles from "./IndustrySelector.module.css";

interface Props {
  industries: Industry[];
  selected: string | null;
  onSelect: (name: string) => void;
}

export default function IndustrySelector({ industries, selected, onSelect }: Props) {
  return (
    <div className={styles.chips} role="tablist" aria-label="산업 선택">
      {industries.map((item) => {
        const active = item.industry === selected;
        return (
          <button
            key={item.industry}
            type="button"
            role="tab"
            aria-selected={active}
            className={active ? `${styles.chip} ${styles.active}` : styles.chip}
            onClick={() => onSelect(item.industry)}
          >
            <span className={styles.name}>{item.industry}</span>
            <span className={styles.count}>{item.report_count}</span>
          </button>
        );
      })}
    </div>
  );
}
