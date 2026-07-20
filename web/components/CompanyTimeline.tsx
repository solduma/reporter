"use client";

import { useEffect, useState } from "react";

import { fetchTimeline, refreshTimeline } from "@/lib/api";
import { broadcastKindLabel } from "@/lib/broadcast";
import type { TimelineItem } from "@/lib/types";

import BroadcastModal from "./BroadcastModal";
import PdfViewer from "./PdfViewer";
import styles from "./CompanyTimeline.module.css";
import SentimentBadge from "./SentimentBadge";

// 3-페이즈 타임라인: 캐시 로드 → 표시+확인중 → 갱신 완료
type Phase =
  | { status: "loading" }
  | { status: "checking"; data: TimelineItem[] }
  | { status: "ready"; data: TimelineItem[] }
  | { status: "error"; message: string };

// 백엔드는 최신순 정렬로 과거 2년치를 반환. 10개씩 페이지네이션으로 '더보기' 하며 넓힌다.
const PAGE_SIZE = 10;

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
  broadcast: "📣 브리핑",
};

function typeClass(type: TimelineItem["type"]): string {
  if (type === "report") {
    return `${styles.item} ${styles.report}`;
  }
  if (type === "disclosure") {
    return `${styles.item} ${styles.disclosure}`;
  }
  return `${styles.item} ${styles.broadcast}`;
}

function badgeClass(type: TimelineItem["type"]): string {
  if (type === "report") {
    return `${styles.typeBadge} ${styles.reportBadge}`;
  }
  if (type === "disclosure") {
    return `${styles.typeBadge} ${styles.disclosureBadge}`;
  }
  return `${styles.typeBadge} ${styles.broadcastBadge}`;
}

function TimelineRow({ item }: { item: TimelineItem }) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const [broadcastOpen, setBroadcastOpen] = useState(false);
  const isReport = item.type === "report";
  const isBroadcast = item.type === "broadcast";
  // 브로드캐스트는 종류 라벨(예: 📌 오후 리서치)을 배지에 노출해 성격을 드러낸다.
  const badgeText = isBroadcast && item.kind ? `📣 ${broadcastKindLabel(item.kind)}` : TYPE_LABEL[item.type];

  const pdfButton = isBroadcast
    ? typeof item.broadcast_id === "number"
      ? (
          <button type="button" className={styles.pdfButton} onClick={() => setBroadcastOpen(true)}>
            브리핑 전문
          </button>
        )
      : null
    : item.report_id !== null
      ? (
          <button type="button" className={styles.pdfButton} onClick={() => setViewerOpen(true)}>
            전체 리포트
          </button>
        )
      : null;
  const originalLink = item.link ? (
    <a className={styles.link} href={item.link} target="_blank" rel="noopener noreferrer">
      {isReport ? "원문 보기" : "공시 원문"}
    </a>
  ) : null;

  return (
    <li className={typeClass(item.type)}>
      {/* 1줄: 제목 */}
      <h3 className={styles.title}>{item.title}</h3>

      {/* 2줄: 타입 배지 · 소스 · 날짜 */}
      <div className={styles.meta}>
        <span className={badgeClass(item.type)}>{badgeText}</span>
        <span className={styles.source}>{item.source}</span>
        <span className={styles.date}>{formatDate(item.date)}</span>
      </div>

      {/* 3줄: 영향 분석(근거) — 좌측, 배지·원문 링크 — 우측 끝 */}
      <div className={styles.footer}>
        <p className={styles.rationale}>{item.rationale}</p>
        <div className={styles.footerRight}>
          {isBroadcast ? null : <SentimentBadge sentiment={item.sentiment} />}
          {pdfButton}
          {originalLink}
        </div>
      </div>

      {viewerOpen && item.report_id !== null ? (
        <PdfViewer reportId={item.report_id} title={item.title} onClose={() => setViewerOpen(false)} />
      ) : null}

      {broadcastOpen && typeof item.broadcast_id === "number" ? (
        <BroadcastModal broadcastId={item.broadcast_id} onClose={() => setBroadcastOpen(false)} />
      ) : null}
    </li>
  );
}

export default function CompanyTimeline({ code }: { code: string }) {
  const [phase, setPhase] = useState<Phase>({ status: "loading" });
  // 노출 개수 — '더보기' 마다 PAGE_SIZE 씩 늘린다. 종목 바뀌면 다시 첫 페이지로.
  const [visible, setVisible] = useState(PAGE_SIZE);

  useEffect(() => {
    let active = true;
    async function load() {
      setPhase({ status: "loading" });
      setVisible(PAGE_SIZE);
      try {
        // Phase 1: 캐시 로드
        const res = await fetchTimeline(code);
        if (!active) return;

        if (res.items.length === 0) {
          setPhase({ status: "ready", data: [] });
          return;
        }

        // Phase 2: 데이터 표시 + 최신공시 확인 중 배지
        setPhase({ status: "checking", data: res.items });

        // Phase 3: DART 신규 공시 확인 (비동기)
        const refreshed = await refreshTimeline(code);
        if (active) {
          setPhase({ status: "ready", data: refreshed.items });
        }
      } catch (e) {
        if (active) {
          setPhase({
            status: "error",
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

  if (phase.status === "loading") {
    return <div className={styles.status}>불러오는 중…</div>;
  }
  if (phase.status === "error") {
    return <p className={styles.error}>API 연결 실패: {phase.message}</p>;
  }
  if (phase.data.length === 0) {
    return <div className={styles.status}>타임라인 데이터가 없습니다</div>;
  }

  const shown = phase.data.slice(0, visible);
  const remaining = phase.data.length - shown.length;
  return (
    <>
      {phase.status === "checking" ? (
        <div className={styles.checkingBadge}>최신 공시 확인 중</div>
      ) : null}
      <ul className={styles.list}>
        {shown.map((item, i) => (
          <TimelineRow key={`${item.type}-${item.report_id ?? item.broadcast_id ?? item.link ?? item.title}-${item.date}-${i}`} item={item} />
        ))}
      </ul>
      {remaining > 0 ? (
        <button
          type="button"
          className={styles.moreButton}
          onClick={() => setVisible((v) => v + PAGE_SIZE)}
        >
          더보기 ({remaining}건)
        </button>
      ) : null}
    </>
  );
}
