"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ReactNode } from "react";

// react-query 기본 staleTime 은 0(즉시 stale)이라 모든 쿼리가 마운트마다 재요청된다.
// reporter 데이터는 대부분 분·시간 단위로 갱신되므로 5분 staleTime 이면 재방문·탭 전환 시
// 캐시 hit 로 즉시 렌더하고, 5분이 지난 뒤에만 백그라운드 재검증한다.
const DEFAULT_STALE_TIME = 5 * 60 * 1000;

export default function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: DEFAULT_STALE_TIME,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}