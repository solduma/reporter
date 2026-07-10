import type { MarketBrief } from "@/lib/types";

import Markdown from "./Markdown";
import styles from "./MarketBriefCard.module.css";

interface Props {
  brief: MarketBrief | null;
}

interface Section {
  header: string; // 이모지 + 제목 (예: "🔥 오늘의 핵심 (3줄)")
  items: string[]; // "→" 항목들 (화살표 제거됨)
  body: string; // 항목 구조가 아닌 자유 문단(폴백용)
}

// 국면 배지 라벨. 서버가 phase 를 안 주는 옛 데이터는 배지 생략.
const PHASE_BADGE: Record<string, string> = {
  forecast: "🔮 개장 전 예상",
  intraday: "📊 장중 실시간",
  closing: "📉 마감 리뷰",
};

function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return parsed.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
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

// 시황은 '이모지 헤더 + → 항목' 고정 구조다. 빈 줄로 섹션을 나누고, 각 섹션의
// 첫 줄을 헤더로, "→"로 시작하는 줄을 항목으로 뽑는다. 구조가 안 맞으면 body 로 폴백.
function parseSections(summary: string): Section[] {
  const blocks = summary
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean);

  return blocks.map((block) => {
    const lines = block.split("\n").map((l) => l.trim());
    const header = lines[0] ?? "";
    const items: string[] = [];
    let current = "";
    for (const line of lines.slice(1)) {
      if (line.startsWith("→")) {
        if (current) {
          items.push(current.trim());
        }
        current = line.replace(/^→\s*/, "");
      } else if (current) {
        current += ` ${line}`; // 항목이 여러 줄에 걸치면 이어붙인다
      }
    }
    if (current) {
      items.push(current.trim());
    }
    // "→" 항목이 없으면 헤더 포함 전체를 자유 문단으로(폴백).
    const body = items.length === 0 ? block : "";
    return { header, items, body };
  });
}

function Section({ section }: { section: Section }) {
  if (section.items.length === 0) {
    // 구조가 안 맞는 블록: 전체를 마크다운 문단으로.
    return (
      <div className={styles.sectionCard}>
        <Markdown content={section.body} className={styles.body} />
      </div>
    );
  }
  return (
    <div className={styles.sectionCard}>
      <h2 className={styles.sectionHeader}>{section.header}</h2>
      <ul className={styles.items}>
        {section.items.map((item, i) => (
          <li key={i} className={styles.item}>
            <Markdown content={item} className={styles.itemText} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function MarketBriefCard({ brief }: Props) {
  const summary = brief?.summary?.trim() ?? "";
  const dateLabel = formatDate(brief?.market_date ?? null);
  const sections = summary ? parseSections(summary) : [];
  const phaseLabel = brief?.phase ? PHASE_BADGE[brief.phase] : "";
  const updatedLabel = formatUpdatedAt(brief?.updated_at);

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h1 className={styles.title}>오늘의 시황</h1>
        {phaseLabel ? (
          <span className={styles.phase} data-phase={brief?.phase}>
            {phaseLabel}
            {updatedLabel ? ` · ${updatedLabel} 기준` : ""}
          </span>
        ) : null}
        {dateLabel ? <span className={styles.date}>{dateLabel}</span> : null}
      </div>
      {sections.length > 0 ? (
        <div className={styles.grid}>
          {sections.map((section, index) => (
            <Section key={index} section={section} />
          ))}
        </div>
      ) : (
        <p className={styles.empty}>오늘의 시황 데이터가 아직 없습니다</p>
      )}
    </section>
  );
}
