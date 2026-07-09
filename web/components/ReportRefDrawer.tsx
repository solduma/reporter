"use client";

import { useState } from "react";

import type { ReportRef, SentimentPoint } from "@/lib/types";

import Markdown from "./Markdown";
import PdfViewer from "./PdfViewer";
import styles from "./ReportRefDrawer.module.css";
import SentimentBadge from "./SentimentBadge";

interface Props {
  point: SentimentPoint | null;
  onClose: () => void;
}

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

export default function ReportRefDrawer({ point, onClose }: Props) {
  const [pdfReport, setPdfReport] = useState<ReportRef | null>(null);

  if (!point) {
    return (
      <aside className={styles.drawer}>
        <p className={styles.empty}>차트의 날짜를 선택하면 해당일 리포트가 여기에 표시됩니다.</p>
      </aside>
    );
  }

  return (
    <aside className={styles.drawer}>
      <div className={styles.header}>
        <div>
          <span className={styles.date}>{formatDate(point.date)}</span>
          <span className={styles.count}>{point.reports.length}건</span>
        </div>
        <button type="button" className={styles.closeButton} onClick={onClose} aria-label="닫기">
          ✕
        </button>
      </div>

      {point.reports.length === 0 ? (
        <p className={styles.empty}>이 날짜에 발행된 리포트가 없습니다.</p>
      ) : (
        <div className={styles.list}>
          {point.reports.map((report) => (
            <article key={report.id} className={styles.card}>
              <div className={styles.topRow}>
                <span className={styles.broker}>{report.broker}</span>
                <SentimentBadge sentiment={report.sentiment} />
              </div>
              <h3 className={styles.title}>{report.title}</h3>
              {report.summary ? <Markdown content={report.summary} className={styles.summary} /> : null}
              {report.has_pdf ? (
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.pdfButton}
                    onClick={() => setPdfReport(report)}
                  >
                    전체 리포트
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}

      {pdfReport ? (
        <PdfViewer
          reportId={pdfReport.id}
          title={pdfReport.title}
          onClose={() => setPdfReport(null)}
        />
      ) : null}
    </aside>
  );
}
