"use client";

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api";
import type { RealtimeQuote } from "@/lib/types";

// 종목 실시간 체결가(KIS WebSocket → SSE)를 구독한다. 국내 6자리 코드만 대상.
// 구독 불가(한도 초과·서버 비활성)나 장 마감이면 null 을 유지 — 호출측은 정적 시세로 표시하면 된다.
export function useRealtimeQuote(code: string | null | undefined): RealtimeQuote | null {
  const [quote, setQuote] = useState<RealtimeQuote | null>(null);

  useEffect(() => {
    setQuote(null);
    if (!code || !/^\d{6}$/.test(code)) {
      return;
    }
    const es = new EventSource(apiUrl(`/api/realtime/quote?code=${code}`));
    es.addEventListener("tick", (e) => {
      try {
        setQuote(JSON.parse((e as MessageEvent).data) as RealtimeQuote);
      } catch {
        // 잘못된 프레임은 무시
      }
    });
    // 서버가 구독 불가를 알리면 스트림을 닫는다(브라우저 자동 재연결 방지).
    es.addEventListener("unavailable", () => es.close());
    return () => es.close();
  }, [code]);

  return quote;
}
