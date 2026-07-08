"use client";

import { useEffect, useState } from "react";

import { fetchTimeline } from "@/lib/api";
import type { TimelineItem } from "@/lib/types";

import PdfViewer from "./PdfViewer";
import styles from "./CompanyTimeline.module.css";
import SentimentBadge from "./SentimentBadge";

// 타임라인은 다른 섹션과 독립적으로 로딩/실패하도록 상태를 분리한다.
type State = { status: "loading" | "ready" | "error"; data: TimelineItem[]; message?: string };

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  });
}

const TYPE_LABEL: Record<TimelineItem["type"], string> = {
  report: "📄 리포트",
  disclosure: "📑 공시",
};

function TimelineRow({ item }: { item: TimelineItem }) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const isReport = item.type === "report";

  return (
    <li className={isReport ? `${styles.item} ${styles.report}` : `${styles.item} ${styles.disclosure}`}>
      <div className={styles.topRow}>
        <span className={isReport ? `${styles.typeBadge} ${styles.reportBadge}` : `${styles.typeBadge} ${styles.disclosureBadge}`}>
          {TYPE_LABEL[item.type]}
        </span>
        <span className={styles.date}>{formatDate(item.date)}</span>
        <SentimentBadge sentiment={item.sentiment} />
      </div>

      <h3 className={styles.title}>{item.title}</h3>

      <div className={styles.meta}>
        <span className={styles.source}>{item.source}</span>
      </div>

      {item.rationale ? <p className={styles.rationale}>{item.rationale}</p> : null}

      {item.report_id !== null || item.link ? (
        <div className={styles.actions}>
          {item.report_id !== null ? (
            <button type="button" className={styles.pdfButton} onClick={() => setViewerOpen(true)}>
              전체 리포트
            </button>
          ) : null}
          {item.link ? (
            <a className={styles.link} href={item.link} target="_blank" rel="noopener noreferrer">
              {isReport ? "원문 보기" : "공시 원문"}
            </a>
          ) : null}
        </div>
      ) : null}

      {viewerOpen && item.report_id !== null ? (
        <PdfViewer reportId={item.report_id} title={item.title} onClose={() => setViewerOpen(false)} />
      ) : null}
    </li>
  );
}

export default function CompanyTimeline({ code }: { code: string }) {
  const [state, setState] = useState<State>({ status: "loading", data: [] });

  useEffect(() => {
    let active = true;
    async function load() {
      setState({ status: "loading", data: [] });
      try {
        const res = await fetchTimeline(code);
        if (active) {
          setState({ status: "ready", data: res });
        }
      } catch (e) {
        if (active) {
          setState({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "타임라인을 불러오지 못했습니다",
          });
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [code]);

  if (state.status === "loading") {
    return <div className={styles.status}>불러오는 중…</div>;
  }
  if (state.status === "error") {
    return <p className={styles.error}>API 연결 실패: {state.message}</p>;
  }
  if (state.data.length === 0) {
    return <div className={styles.status}>타임라인 데이터가 없습니다</div>;
  }

  return (
    <ul className={styles.list}>
      {state.data.map((item, i) => (
        <TimelineRow key={`${item.type}-${item.report_id ?? item.link ?? item.title}-${item.date}-${i}`} item={item} />
      ))}
    </ul>
  );
}
