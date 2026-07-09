"use client";

import Link from "next/link";

import StockSearch from "@/components/StockSearch";

import styles from "./page.module.css";

const QUICK_PICKS = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
  { code: "035420", name: "NAVER" },
  { code: "035720", name: "카카오" },
  { code: "005380", name: "현대차" },
  { code: "051910", name: "LG화학" },
] as const;

export default function CompaniesPage() {
  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>기업 분석</h1>
        <p className={styles.subtitle}>종목명 또는 코드로 검색해 분석하세요</p>
      </header>

      <StockSearch />

      <div className={styles.quickHead}>자주 찾는 종목</div>
      <div className={styles.grid}>
        {QUICK_PICKS.map((company) => (
          <Link key={company.code} href={`/companies/${company.code}`} className={styles.card}>
            <span className={styles.name}>{company.name}</span>
            <span className={styles.code}>{company.code}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
