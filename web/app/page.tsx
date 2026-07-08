"use client";

import { useEffect, useState } from "react";

import MarketBriefCard from "@/components/MarketBriefCard";
import MarketOverviewCard from "@/components/MarketOverviewCard";
import ReportColumn from "@/components/ReportColumn";
import { fetchMarketBrief, fetchReports } from "@/lib/api";
import type { MarketBrief, Report } from "@/lib/types";

import styles from "./page.module.css";

export default function TodaysBrewPage() {
  const [brief, setBrief] = useState<MarketBrief | null>(null);
  const [industry, setIndustry] = useState<Report[]>([]);
  const [company, setCompany] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [marketRes, industryRes, companyRes] = await Promise.all([
          fetchMarketBrief(),
          fetchReports("industry"),
          fetchReports("company"),
        ]);
        if (!active) {
          return;
        }
        setBrief(marketRes);
        setIndustry(industryRes);
        setCompany(companyRes);
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "데이터를 불러오지 못했습니다");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className={styles.page}>
      <MarketOverviewCard />

      <MarketBriefCard brief={brief} />

      {error ? (
        <p className={styles.error}>API 연결 실패: {error}</p>
      ) : null}

      <div className={styles.columns}>
        <ReportColumn title="산업분석" icon="🏭" reports={industry} loading={loading} />
        <ReportColumn title="종목분석" icon="📈" reports={company} loading={loading} />
      </div>
    </div>
  );
}
