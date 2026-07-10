"use client";

import Link from "next/link";

import StockSearch from "@/components/StockSearch";
import { removeQuickPick, useQuickPicks } from "@/lib/quickPicks";

import styles from "./page.module.css";

export default function CompaniesPage() {
  const picks = useQuickPicks();

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>기업 분석</h1>
        <p className={styles.subtitle}>종목명 또는 코드로 검색해 분석하세요</p>
      </header>

      <StockSearch />

      <div className={styles.quickHead}>자주 찾는 종목</div>
      {picks.length === 0 ? (
        <p className={styles.empty}>조회한 종목이 여기에 쌓입니다. 위에서 검색해 보세요.</p>
      ) : (
        <div className={styles.grid}>
          {picks.map((company) => (
            <div key={company.code} className={styles.cardWrap}>
              <Link href={`/companies/${company.code}`} className={styles.card}>
                <span className={styles.name}>{company.name}</span>
                <span className={styles.code}>{company.code}</span>
              </Link>
              <button
                type="button"
                className={styles.remove}
                aria-label={`${company.name} 제거`}
                title="목록에서 제거"
                onClick={() => removeQuickPick(company.code)}
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
