"use client";

import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { reportPdfUrl } from "@/lib/api";

import styles from "./PdfViewer.module.css";

// public/ 로 복사해 둔 pdf.js worker (버전은 pdfjs-dist와 일치).
// scripts/copy-pdf-worker.mjs 가 install/build 시 자동 복사한다.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

export default function PdfDocument({ reportId }: { reportId: number }) {
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const fileUrl = reportPdfUrl(reportId);

  const goPrev = () => setPageNumber((p) => Math.max(1, p - 1));
  const goNext = () => setPageNumber((p) => Math.min(numPages || 1, p + 1));

  return (
    <div className={styles.viewer}>
      <div className={styles.canvasWrap}>
        <Document
          file={fileUrl}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            setPageNumber(1);
            setError(null);
          }}
          onLoadError={(e) => setError(e.message)}
          loading={<div className={styles.status}>PDF 불러오는 중…</div>}
          error={<div className={styles.status}>PDF를 불러오지 못했습니다.</div>}
        >
          <Page
            pageNumber={pageNumber}
            width={760}
            renderAnnotationLayer={false}
            renderTextLayer={false}
          />
        </Document>
      </div>

      {error ? <p className={styles.errorText}>{error}</p> : null}

      {numPages > 0 ? (
        <div className={styles.controls}>
          <button
            type="button"
            className={styles.navButton}
            onClick={goPrev}
            disabled={pageNumber <= 1}
          >
            이전
          </button>
          <span className={styles.pageInfo}>
            {pageNumber} / {numPages}
          </span>
          <button
            type="button"
            className={styles.navButton}
            onClick={goNext}
            disabled={pageNumber >= numPages}
          >
            다음
          </button>
        </div>
      ) : null}
    </div>
  );
}
