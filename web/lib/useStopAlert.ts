"use client";

import { useEffect, useState } from "react";

import { fetchPortfolio } from "@/lib/api";

// 손절 경보 건수(도달 hit + 근접 near). 네비 뱃지로 노출해, 포트폴리오를 안 열어도 인지시킨다.
// 마운트 시 1회 조회(실패 시 0 — 뱃지 생략). 손절은 종가 기준이라 실시간 폴링 불요.
export function useStopAlertCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let active = true;
    fetchPortfolio()
      .then((v) => {
        if (active) {
          setCount(v.summary.stop_hit + v.summary.stop_near);
        }
      })
      .catch(() => {
        /* 조회 실패는 뱃지 생략으로 흡수 */
      });
    return () => {
      active = false;
    };
  }, []);

  return count;
}
