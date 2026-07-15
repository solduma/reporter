"use client";

import { useEffect, useState } from "react";

import { fetchCalendar } from "@/lib/api";
import type { CalendarEvent, CalendarKind, CalendarRegion, CalendarView } from "@/lib/types";

import styles from "./page.module.css";

// 이벤트 종류 배지 라벨·색 구분(kind). fomc/선거는 강조 톤.
const KIND_META: Record<CalendarKind, { label: string; cls: string }> = {
  macro: { label: "매크로", cls: styles.kindMacro },
  earnings: { label: "실적", cls: styles.kindEarnings },
  fomc: { label: "FOMC", cls: styles.kindFomc },
  election: { label: "선거", cls: styles.kindElection },
  geo: { label: "지정학", cls: styles.kindGeo },
};
const REGION_LABEL: Record<CalendarRegion, string> = { US: "🇺🇸 미국", KR: "🇰🇷 한국", GLOBAL: "🌐 글로벌" };

const REGION_FILTERS: { value: CalendarRegion | ""; label: string }[] = [
  { value: "", label: "전체" },
  { value: "US", label: "미국" },
  { value: "KR", label: "한국" },
];
const KIND_FILTERS: { value: CalendarKind | ""; label: string }[] = [
  { value: "", label: "전체" },
  { value: "macro", label: "매크로" },
  { value: "fomc", label: "FOMC" },
  { value: "earnings", label: "실적" },
  { value: "election", label: "선거" },
];

function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  const wd = ["일", "월", "화", "수", "목", "금", "토"][d.getDay()];
  return `${iso.slice(5)} (${wd})`;
}

function Stars({ n }: { n: number }) {
  return <span className={styles.stars}>{"★".repeat(n)}{"☆".repeat(3 - n)}</span>;
}

function EventCard({ ev }: { ev: CalendarEvent }) {
  const kind = KIND_META[ev.kind] ?? KIND_META.macro;
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <span className={styles.date}>{fmtDate(ev.event_date)}</span>
        <span className={`${styles.kindBadge} ${kind.cls}`}>{kind.label}</span>
        <span className={styles.region}>{REGION_LABEL[ev.region] ?? ev.region}</span>
        <Stars n={ev.importance} />
      </div>
      <div className={styles.title}>{ev.title}</div>
      {/* 수치: 과거는 실적치·직전치, 미래는 예상치(있으면). */}
      {(ev.actual || ev.previous || ev.consensus) && (
        <div className={styles.metrics}>
          {ev.actual && <span>실적 <b>{ev.actual}</b></span>}
          {ev.consensus && <span>예상 {ev.consensus}</span>}
          {ev.previous && <span>직전 {ev.previous}</span>}
        </div>
      )}
      {/* LLM 텍스트: 과거=영향·이유, 미래=기대치. 아직 생성 전이면 생략. */}
      {ev.is_past && ev.impact_text && (
        <p className={styles.impact}><span className={styles.impactLabel}>지수 영향</span>{ev.impact_text}</p>
      )}
      {!ev.is_past && ev.expectation_text && (
        <p className={styles.expect}><span className={styles.expectLabel}>시장 기대</span>{ev.expectation_text}</p>
      )}
    </div>
  );
}

export default function CalendarPage() {
  const [region, setRegion] = useState<CalendarRegion | "">("");
  const [kind, setKind] = useState<CalendarKind | "">("");
  const [data, setData] = useState<CalendarView | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let active = true;
    setStatus("loading");
    fetchCalendar({ region: region || undefined, kind: kind || undefined })
      .then((v) => {
        if (active) {
          setData(v);
          setStatus("ready");
        }
      })
      .catch(() => active && setStatus("error"));
    return () => {
      active = false;
    };
  }, [region, kind]);

  const upcoming = data?.upcoming ?? [];
  const past = data?.past ?? [];
  const empty = status === "ready" && upcoming.length === 0 && past.length === 0;

  return (
    <main className={styles.page}>
      <h1 className={styles.h1}>경제 · 실적 캘린더</h1>
      <p className={styles.sub}>지수 영향이 큰 매크로 지표·중대일. 다가올 이벤트는 시장 기대, 지나간 이벤트는 지수 영향과 이유.</p>

      <div className={styles.filters}>
        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>지역</span>
          {REGION_FILTERS.map((f) => (
            <button
              key={f.value || "all"}
              type="button"
              className={region === f.value ? `${styles.chip} ${styles.chipOn}` : styles.chip}
              onClick={() => setRegion(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className={styles.filterGroup}>
          <span className={styles.filterLabel}>종류</span>
          {KIND_FILTERS.map((f) => (
            <button
              key={f.value || "all"}
              type="button"
              className={kind === f.value ? `${styles.chip} ${styles.chipOn}` : styles.chip}
              onClick={() => setKind(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {status === "loading" && <div className={styles.status}>불러오는 중…</div>}
      {status === "error" && <div className={styles.error}>캘린더를 불러오지 못했습니다.</div>}
      {empty && <div className={styles.status}>해당 조건의 이벤트가 없습니다.</div>}

      {status === "ready" && !empty && (
        <div className={styles.columns}>
          <section className={styles.col}>
            <h2 className={styles.colTitle}>다가올 이벤트 <span className={styles.count}>{upcoming.length}</span></h2>
            {upcoming.map((e) => (
              <EventCard key={`${e.event_date}-${e.title}`} ev={e} />
            ))}
            {upcoming.length === 0 && <div className={styles.status}>예정된 이벤트 없음</div>}
          </section>
          <section className={styles.col}>
            <h2 className={styles.colTitle}>지나간 이벤트 <span className={styles.count}>{past.length}</span></h2>
            {past.map((e) => (
              <EventCard key={`${e.event_date}-${e.title}`} ev={e} />
            ))}
            {past.length === 0 && <div className={styles.status}>지난 이벤트 없음</div>}
          </section>
        </div>
      )}
    </main>
  );
}
