"use client";

import { useState } from "react";

import { GLOSSARY } from "@/lib/glossary";

import styles from "./InfoDot.module.css";

// 지표 옆 ⓘ — 호버(데스크톱)·탭(모바일)으로 용어 설명 + 해석 기준을 띄운다.
// termKey 가 glossary 에 있으면 그 내용을, 없으면 what/guide 직접 전달분을 쓴다.
export default function InfoDot({
  termKey,
  what,
  guide,
}: {
  termKey?: keyof typeof GLOSSARY;
  what?: string;
  guide?: string;
}) {
  const [open, setOpen] = useState(false);
  const entry = termKey ? GLOSSARY[termKey] : undefined;
  const whatText = what ?? entry?.what;
  const guideText = guide ?? entry?.guide;
  if (!whatText) {
    return null;
  }

  return (
    <span
      className={styles.wrap}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className={styles.dot}
        aria-label="설명 보기"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation(); // 표 행 클릭(종목 이동) 등과 분리
          setOpen((v) => !v);
        }}
      >
        i
      </button>
      {open ? (
        <span className={styles.pop} role="tooltip">
          <span className={styles.what}>{whatText}</span>
          {guideText ? <span className={styles.guide}>{guideText}</span> : null}
        </span>
      ) : null}
    </span>
  );
}
