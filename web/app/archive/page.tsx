"use client";

import { useEffect, useState } from "react";

import BroadcastModal from "@/components/BroadcastModal";
import { fetchBroadcasts } from "@/lib/api";
import { BROADCAST_KIND_LABEL, broadcastKindLabel } from "@/lib/broadcast";
import type { BroadcastKind, BroadcastRef } from "@/lib/types";

import styles from "./page.module.css";

const PAGE_SIZE = 50;

// 필터 칩 목록. null = 전체.
const KIND_FILTERS: { label: string; value: BroadcastKind | null }[] = [
  { label: "전체", value: null },
  ...(Object.keys(BROADCAST_KIND_LABEL) as BroadcastKind[]).map((k) => ({
    label: BROADCAST_KIND_LABEL[k],
    value: k,
  })),
];

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ArchivePage() {
  const [kind, setKind] = useState<BroadcastKind | null>(null);
  const [items, setItems] = useState<BroadcastRef[]>([]);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setStatus("loading");
      setMessage(null);
      try {
        // hasMore 판정을 위해 PAGE_SIZE+1 건 요청.
        const res = await fetchBroadcasts({
          kind: kind ?? undefined,
          limit: PAGE_SIZE + 1,
          offset: page * PAGE_SIZE,
        });
        if (active) {
          setHasMore(res.length > PAGE_SIZE);
          setItems(res.slice(0, PAGE_SIZE));
          setStatus("ready");
        }
      } catch (e) {
        if (active) {
          setStatus("error");
          setMessage(e instanceof Error ? e.message : "브리핑을 불러오지 못했습니다");
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [kind, page]);

  const selectKind = (value: BroadcastKind | null) => {
    setKind(value);
    setPage(0);
  };

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>브리핑 아카이브</h1>
        <p className={styles.subtitle}>
          텔레그램으로 발송된 시황·투자·경제·채권 종합, 장중 뉴스, 미국증시, 오후 리서치 이력
        </p>
      </header>

      <div className={styles.chips} role="tablist" aria-label="브리핑 종류">
        {KIND_FILTERS.map((f) => {
          const active = f.value === kind;
          return (
            <button
              key={f.value ?? "all"}
              type="button"
              role="tab"
              aria-selected={active}
              className={active ? `${styles.chip} ${styles.chipActive}` : styles.chip}
              onClick={() => selectKind(f.value)}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {status === "loading" ? <p className={styles.status}>불러오는 중…</p> : null}
      {status === "error" ? <p className={styles.error}>API 연결 실패: {message}</p> : null}
      {status === "ready" && items.length === 0 ? (
        <p className={styles.status}>
          아직 아카이브된 브리핑이 없습니다. (배포 이후 발송분부터 축적됩니다)
        </p>
      ) : null}

      {status === "ready" && items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {items.map((b) => (
              <li key={b.id}>
                <button type="button" className={styles.card} onClick={() => setOpenId(b.id)}>
                  <div className={styles.cardHead}>
                    <span className={styles.kind}>{broadcastKindLabel(b.kind)}</span>
                    <span className={styles.date}>{formatDateTime(b.sent_at)}</span>
                  </div>
                  {b.snippet ? <p className={styles.snippet}>{b.snippet}</p> : null}
                  {b.industries.length > 0 || b.stock_codes.length > 0 ? (
                    <div className={styles.tags}>
                      {b.industries.map((ind) => (
                        <span key={`i-${ind}`} className={styles.tagIndustry}>
                          {ind}
                        </span>
                      ))}
                      {b.stock_codes.map((code) => (
                        <span key={`s-${code}`} className={styles.tagStock}>
                          {code}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>

          <div className={styles.pager}>
            <button
              type="button"
              className={styles.pagerButton}
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              ◀ 이전
            </button>
            <span className={styles.pageInfo}>{page + 1} 페이지</span>
            <button
              type="button"
              className={styles.pagerButton}
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              다음 ▶
            </button>
          </div>
        </>
      ) : null}

      {openId !== null ? (
        <BroadcastModal broadcastId={openId} onClose={() => setOpenId(null)} />
      ) : null}
    </div>
  );
}
