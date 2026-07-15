"use client";

import { useEffect, useRef, useState } from "react";

// localStorage 에 사용자 선택을 저장·복원하는 useState 대체물. 초기값은 저장된 값이 있으면 그것을,
// 없으면 defaultValue 를 쓴다. 값이 바뀔 때마다 저장한다(JSON 직렬화). SSR/스토리지 예외는 흡수.
// 스크리너 필터처럼 "사용자가 고른 조건을 다음 방문에도 유지"하려는 상태에 쓴다.
//
// 복원은 첫 클라이언트 렌더에서 동기적으로(lazy initializer) 수행한다 — effect 로 복원하면
// 저장 effect 가 먼저 defaultValue 로 덮어쓰는 경합이 생긴다. 필터 칩은 SSR 산출물에 실질 영향이
// 없어 하이드레이션 불일치 우려가 없다.
export function usePersistentState<T>(
  key: string,
  defaultValue: T,
): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") {
      return defaultValue; // SSR: 저장값 접근 불가
    }
    try {
      const raw = window.localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : defaultValue;
    } catch {
      return defaultValue; // 접근/파싱 실패는 defaultValue 로 흡수
    }
  });

  // 최초 렌더에서 defaultValue 를 되쓰지 않도록(저장값 == default 여도 무해하지만) 첫 저장을 건너뛴다.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* 저장 실패(용량·프라이빗 모드 등)는 무시 */
    }
  }, [key, value]);

  return [value, setValue];
}
