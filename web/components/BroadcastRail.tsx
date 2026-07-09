"use client";

import { useEffect, useState } from "react";

import { fetchBroadcasts } from "@/lib/api";
import type { BroadcastQuery } from "@/lib/api";
import { broadcastKindLabel } from "@/lib/broadcast";
import type { BroadcastRef } from "@/lib/types";

import BroadcastModal from "./BroadcastModal";
import styles from "./BroadcastRail.module.css";

interface Props {
  // industry 또는 stock 중 하나로 필터. 둘 다 없으면 전체 최신.
  query: BroadcastQuery;
  title?: string;
  emptyText?: string;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });
}

export default function BroadcastRail({ query, title = "📣 관련 브리핑", emptyText }: Props) {
  const [items, setItems] = useState<BroadcastRef[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  // 필터 값(문자열)로 재요청. 객체 참조 변화에 흔들리지 않게 원시값을 의존성으로.
  const { industry, stock, kind } = query;

  useEffect(() => {
    let active = true;
    async function load() {
      setStatus("loading");
      setMessage(null);
      try {
        const res = await fetchBroadcasts({ industry, stock, kind, limit: 30 });
        if (active) {
          setItems(res);
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
  }, [industry, stock, kind]);

  return (
    <section className={styles.rail}>
      <h3 className={styles.title}>{title}</h3>

      {status === "loading" ? <p className={styles.status}>불러오는 중…</p> : null}
      {status === "error" ? <p className={styles.error}>{message}</p> : null}
      {status === "ready" && items.length === 0 ? (
        <p className={styles.status}>{emptyText ?? "관련 브리핑이 아직 없습니다."}</p>
      ) : null}

      {status === "ready" && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((b) => (
            <li key={b.id} className={styles.card}>
              <button type="button" className={styles.cardButton} onClick={() => setOpenId(b.id)}>
                <div className={styles.cardHead}>
                  <span className={styles.kind}>{broadcastKindLabel(b.kind)}</span>
                  <span className={styles.date}>{formatDate(b.ref_date)}</span>
                </div>
                {b.snippet ? <p className={styles.snippet}>{b.snippet}</p> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {openId !== null ? (
        <BroadcastModal broadcastId={openId} onClose={() => setOpenId(null)} />
      ) : null}
    </section>
  );
}
