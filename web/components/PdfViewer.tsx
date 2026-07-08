"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";

import styles from "./PdfViewer.module.css";

// react-pdf(pdf.js)는 브라우저 전용 API(DOMMatrix 등)에 의존하므로
// SSR을 끄고 클라이언트에서만 로드한다.
const PdfDocument = dynamic(() => import("./PdfDocument"), {
  ssr: false,
  loading: () => <div className={styles.status}>뷰어 불러오는 중…</div>,
});

interface Props {
  reportId: number;
  title: string;
  onClose: () => void;
}

export default function PdfViewer({ reportId, title, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div
        className={styles.modal}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className={styles.header}>
          <span className={styles.headerTitle}>{title}</span>
          <button
            type="button"
            className={styles.closeButton}
            onClick={onClose}
            aria-label="닫기"
          >
            ✕
          </button>
        </header>
        <div className={styles.body}>
          <PdfDocument reportId={reportId} />
        </div>
      </div>
    </div>
  );
}
