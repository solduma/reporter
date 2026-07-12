"use client";

import { useEffect } from "react";

import { hasSeenTour, startTour } from "@/lib/tour";
import type { TourId } from "@/lib/tour";

// 첫 방문 시 1회 자동으로 투어를 시작한다. ready=false 이면(데이터 로딩 중) 대상 요소가
// 아직 없을 수 있어 대기한다. 약간의 지연으로 레이아웃이 자리잡은 뒤 실행한다.
export function useAutoTour(id: TourId, ready: boolean): void {
  useEffect(() => {
    if (!ready || hasSeenTour(id)) {
      return;
    }
    const t = setTimeout(() => startTour(id), 400);
    return () => clearTimeout(t);
  }, [id, ready]);
}
