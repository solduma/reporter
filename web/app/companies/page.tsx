"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

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
  const router = useRouter();
  const [code, setCode] = useState("");

  const trimmed = code.trim();

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmed) {
      router.push(`/companies/${encodeURIComponent(trimmed)}`);
    }
  }

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>기업 분석</h1>
        <p className={styles.subtitle}>종목 코드로 캔들 차트를 조회하세요</p>
      </header>

      <form className={styles.searchForm} onSubmit={handleSubmit}>
        <input
          className={styles.input}
          type="text"
          inputMode="numeric"
          placeholder="종목 코드 (예: 005930)"
          aria-label="종목 코드"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button className={styles.submit} type="submit" disabled={!trimmed}>
          조회
        </button>
      </form>

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
