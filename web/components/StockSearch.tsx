"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { searchStocks } from "@/lib/api";
import type { StockSearchHit } from "@/lib/types";

import styles from "./StockSearch.module.css";

const DEBOUNCE_MS = 200;

function formatCap(cap: number | null): string {
  if (!cap) {
    return "";
  }
  const jo = cap / 1e12; // 조
  if (jo >= 1) {
    return `${jo.toFixed(1)}조`;
  }
  return `${Math.round(cap / 1e8).toLocaleString()}억`;
}

export default function StockSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<StockSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0); // 키보드 하이라이트 인덱스
  const boxRef = useRef<HTMLDivElement>(null);

  const trimmed = query.trim();

  // 디바운스 검색. 입력이 비면 후보를 비운다.
  useEffect(() => {
    if (!trimmed) {
      setHits([]);
      setOpen(false);
      return;
    }
    let activeReq = true;
    const timer = setTimeout(() => {
      void searchStocks(trimmed)
        .then((res) => {
          if (activeReq) {
            setHits(res);
            setOpen(true);
            setActive(0);
          }
        })
        .catch(() => {
          if (activeReq) {
            setHits([]);
          }
        });
    }, DEBOUNCE_MS);
    return () => {
      activeReq = false;
      clearTimeout(timer);
    };
  }, [trimmed]);

  // 바깥 클릭 시 드롭다운 닫기.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function goto(code: string) {
    if (code) {
      router.push(`/companies/${encodeURIComponent(code)}`);
    }
  }

  // '분석' 버튼: 후보 최상단(또는 하이라이트)을, 코드 직접입력이면 그 코드를 분석.
  function analyze() {
    if (hits.length > 0) {
      goto(hits[active]?.stock_code ?? hits[0].stock_code);
    } else if (/^\d{6}$/.test(trimmed)) {
      goto(trimmed);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || hits.length === 0) {
      if (e.key === "Enter") {
        analyze();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      goto(hits[active]?.stock_code ?? hits[0].stock_code);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const canAnalyze = hits.length > 0 || /^\d{6}$/.test(trimmed);

  return (
    <div className={styles.wrap} ref={boxRef}>
      <div className={styles.bar}>
        <input
          className={styles.input}
          type="text"
          placeholder="종목명 또는 코드 검색 (예: 삼성전자, 005930)"
          aria-label="종목 검색"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => hits.length > 0 && setOpen(true)}
        />
        <button
          type="button"
          className={styles.analyze}
          disabled={!canAnalyze}
          onClick={analyze}
        >
          분석
        </button>
      </div>

      {open && hits.length > 0 ? (
        <ul className={styles.dropdown} role="listbox">
          {hits.map((h, i) => (
            <li key={h.stock_code}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                className={i === active ? `${styles.option} ${styles.optionActive}` : styles.option}
                onMouseEnter={() => setActive(i)}
                onClick={() => goto(h.stock_code)}
              >
                <span className={styles.optName}>{h.stock_name}</span>
                <span className={styles.optCode}>{h.stock_code}</span>
                <span className={styles.optMarket}>{h.market}</span>
                {formatCap(h.market_cap) ? (
                  <span className={styles.optCap}>{formatCap(h.market_cap)}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
