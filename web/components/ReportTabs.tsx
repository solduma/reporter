"use client";

import { useState } from "react";

import type { Report } from "@/lib/types";

import ReportColumn from "./ReportColumn";
import styles from "./ReportTabs.module.css";

interface Tab {
  key: string;
  label: string;
  icon: string;
  reports: Report[];
}

interface Props {
  industry: Report[];
  company: Report[];
  loading: boolean;
}

export default function ReportTabs({ industry, company, loading }: Props) {
  const tabs: Tab[] = [
    { key: "industry", label: "산업 리포트", icon: "🏭", reports: industry },
    { key: "company", label: "종목 리포트", icon: "📈", reports: company },
  ];
  const [activeKey, setActiveKey] = useState(tabs[0].key);

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[0];

  return (
    <div className={styles.wrapper}>
      <div className={styles.tabList} role="tablist" aria-label="리포트 종류">
        {tabs.map((tab) => {
          const selected = tab.key === activeKey;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={`tab-${tab.key}`}
              aria-selected={selected}
              aria-controls={`panel-${tab.key}`}
              className={`${styles.tab} ${selected ? styles.tabActive : ""}`}
              onClick={() => setActiveKey(tab.key)}
            >
              <span className={styles.icon}>{tab.icon}</span>
              <span className={styles.label}>{tab.label}</span>
              {!loading ? <span className={styles.count}>{tab.reports.length}</span> : null}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`panel-${activeTab.key}`}
        aria-labelledby={`tab-${activeTab.key}`}
      >
        <ReportColumn reports={activeTab.reports} loading={loading} showHeader={false} />
      </div>
    </div>
  );
}
