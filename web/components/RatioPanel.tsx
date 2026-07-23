"use client";

import { useEffect, useState } from "react";

import InfoDot from "@/components/InfoDot";
import { fetchCompanyRatios } from "@/lib/api";
import type { RatioItem } from "@/lib/types";

import styles from "./RatioPanel.module.css";

const CATEGORIES = [
  { key: "profitability", label: "수익성" },
  { key: "liquidity", label: "유동성" },
  { key: "leverage", label: "안정성" },
  { key: "valuation", label: "밸류에이션" },
];

interface Props {
  code: string;
}

// 온톨로지 RatioEngine 으로 계산한 57개 재무비율을 카테고리 탭으로 노출(C1).
// 결측 항목은 ok=false 와 reason 을 표시한다.
export default function RatioPanel({ code }: Props) {
  const [active, setActive] = useState("profitability");
  const [ratios, setRatios] = useState<RatioItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchCompanyRatios(code)
      .then((res) => {
        if (alive) setRatios(res.items);
      })
      .catch(() => {
        if (alive) setRatios([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [code]);

  const filtered = ratios.filter((r) => r.category === active);

  return (
    <div className={styles.panel}>
      <div className={styles.tabs}>
        {CATEGORIES.map((c) => (
          <button
            key={c.key}
            type="button"
            className={active === c.key ? styles.tabActive : styles.tab}
            onClick={() => setActive(c.key)}
            aria-pressed={active === c.key}
          >
            {c.label}
          </button>
        ))}
      </div>
      {loading ? (
        <div className={styles.status}>불러오는 중…</div>
      ) : filtered.length === 0 ? (
        <div className={styles.status}>해당 카테고리 데이터가 없습니다</div>
      ) : (
        <div className={styles.grid}>
          {filtered.map((r) => (
            <div key={r.ratio_id} className={styles.item}>
              <span className={styles.name}>
                {r.korean_name}
                {r.description ? <InfoDot what={r.description} /> : null}
              </span>
              <span className={styles.value}>
                {r.ok && r.value !== null
                  ? `${r.value}${r.unit ? ` ${r.unit}` : ""}`
                  : r.reason || "-"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
