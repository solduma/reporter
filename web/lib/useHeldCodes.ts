"use client";

import { useEffect, useState } from "react";

import { fetchHoldings } from "@/lib/api";

// 보유 중인 종목코드 집합. 스크리너·검색 결과에 "보유" 배지를 붙일 때 쓴다.
// 한 번만 조회하고(마운트 시) Set 으로 O(1) 조회. 실패 시 빈 집합(배지만 안 뜸, 기능 무영향).
export function useHeldCodes(): Set<string> {
  const [codes, setCodes] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    fetchHoldings()
      .then((hs) => {
        if (active) {
          setCodes(new Set(hs.map((h) => h.stock_code)));
        }
      })
      .catch(() => {
        /* 보유 조회 실패는 배지 생략으로 흡수(스크리너·검색 본기능과 무관) */
      });
    return () => {
      active = false;
    };
  }, []);

  return codes;
}
