"use client";

import { useCallback } from "react";

import styles from "./DateRangeSlider.module.css";

interface Props {
  // 선택 가능한 날짜(오름차순 'YYYY-MM-DD'). 로드된 봉의 날짜 축.
  dates: string[];
  from: string; // 현재 시작일
  to: string; // 현재 종료일
  onChange: (from: string, to: string) => void;
}

// 시작일·종료일 두 thumb 를 각각 움직이는 date-range 슬라이더. 차트 표시 구간을 뜻한다.
// 두 개의 range input 을 겹쳐 dual-thumb 를 구현하고, 값은 dates 배열의 인덱스로 다룬다.
export default function DateRangeSlider({ dates, from, to, onChange }: Props) {
  const max = dates.length - 1;
  const fromIdx = Math.max(0, dates.indexOf(from));
  // to 가 정확히 없으면(범위 밖) 마지막으로.
  const toIdx = to && dates.indexOf(to) >= 0 ? dates.indexOf(to) : max;

  const handleFrom = useCallback(
    (v: number) => {
      const lo = Math.min(v, toIdx); // 시작이 종료를 넘지 않게
      onChange(dates[lo], dates[toIdx]);
    },
    [dates, toIdx, onChange],
  );
  const handleTo = useCallback(
    (v: number) => {
      const hi = Math.max(v, fromIdx);
      onChange(dates[fromIdx], dates[hi]);
    },
    [dates, fromIdx, onChange],
  );

  if (dates.length < 2) {
    return null;
  }

  const pctLo = (fromIdx / max) * 100;
  const pctHi = (toIdx / max) * 100;

  return (
    <div className={styles.wrap}>
      <span className={styles.label}>{from}</span>
      <div className={styles.track}>
        <div
          className={styles.fill}
          style={{ left: `${pctLo}%`, right: `${100 - pctHi}%` }}
        />
        <input
          type="range"
          min={0}
          max={max}
          value={fromIdx}
          onChange={(e) => handleFrom(Number(e.target.value))}
          className={styles.range}
          aria-label="시작일"
        />
        <input
          type="range"
          min={0}
          max={max}
          value={toIdx}
          onChange={(e) => handleTo(Number(e.target.value))}
          className={styles.range}
          aria-label="종료일"
        />
      </div>
      <span className={styles.label}>{to}</span>
    </div>
  );
}
