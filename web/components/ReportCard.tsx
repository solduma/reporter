"use client";

import { useState } from "react";

import type { Report } from "@/lib/types";

import Markdown from "./Markdown";
import PdfViewer from "./PdfViewer";
import styles from "./ReportCard.module.css";
import SentimentBadge from "./SentimentBadge";

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

export default function ReportCard({ report }: { report: Report }) {
  const [viewerOpen, setViewerOpen] = useState(false);

  return (
    <article className={styles.card}>
      <div className={styles.topRow}>
        <span className={styles.name}>{report.name ?? "이름 미상"}</span>
        <SentimentBadge sentiment={report.sentiment} />
      </div>

      <h3 className={styles.title}>{report.title}</h3>

      <div className={styles.meta}>
        <span>{report.broker}</span>
        <span className={styles.dot}>·</span>
        <span>{formatDate(report.published_date)}</span>
      </div>

      {report.summary ? <Markdown content={report.summary} className={styles.summary} /> : null}

      {report.rationale ? <p className={styles.rationale}>{report.rationale}</p> : null}

      {report.has_pdf ? (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.pdfButton}
            onClick={() => setViewerOpen(true)}
          >
            전체 리포트
          </button>
        </div>
      ) : null}

      {viewerOpen ? (
        <PdfViewer
          reportId={report.id}
          title={report.title}
          onClose={() => setViewerOpen(false)}
        />
      ) : null}
    </article>
  );
}
