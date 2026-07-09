"use client";

import { useEffect, useState } from "react";

import { fetchBroadcast } from "@/lib/api";
import { broadcastKindLabel } from "@/lib/broadcast";
import type { BroadcastDetail } from "@/lib/types";

import styles from "./BroadcastModal.module.css";

interface Props {
  broadcastId: number;
  onClose: () => void;
}

function formatSentAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function BroadcastModal({ broadcastId, onClose }: Props) {
  const [detail, setDetail] = useState<BroadcastDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setDetail(null);
      setError(null);
      try {
        const res = await fetchBroadcast(broadcastId);
        if (active) {
          setDetail(res);
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "브로드캐스트를 불러오지 못했습니다");
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [broadcastId]);

  const reports = detail?.source_refs.reports ?? [];
  const news = detail?.source_refs.news ?? [];

  return (
    <div className={styles.backdrop} onClick={onClose} role="presentation">
      <div
        className={styles.modal}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className={styles.header}>
          <div>
            <span className={styles.kind}>
              {detail ? broadcastKindLabel(detail.kind) : "…"}
            </span>
            {detail ? <span className={styles.sentAt}>{formatSentAt(detail.sent_at)}</span> : null}
          </div>
          <button type="button" className={styles.closeButton} onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>

        {error ? <p className={styles.error}>{error}</p> : null}
        {!detail && !error ? <p className={styles.loading}>불러오는 중…</p> : null}

        {detail ? (
          <>
            <pre className={styles.body}>{detail.body}</pre>

            {reports.length > 0 ? (
              <section className={styles.sources}>
                <h4 className={styles.sourcesTitle}>📚 근거 리포트</h4>
                <ul className={styles.sourceList}>
                  {reports.map((r, i) => (
                    <li key={`r-${i}`}>
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noopener noreferrer">
                          [{r.broker}] {r.title}
                        </a>
                      ) : (
                        <span>
                          [{r.broker}] {r.title}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {news.length > 0 ? (
              <section className={styles.sources}>
                <h4 className={styles.sourcesTitle}>🗞 관련 기사</h4>
                <ul className={styles.sourceList}>
                  {news.map((n, i) => (
                    <li key={`n-${i}`}>
                      {n.url ? (
                        <a href={n.url} target="_blank" rel="noopener noreferrer">
                          {n.title} ({n.source})
                        </a>
                      ) : (
                        <span>
                          {n.title} ({n.source})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
