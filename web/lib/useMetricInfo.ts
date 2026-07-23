"use client";

import { useEffect, useState } from "react";

import { fetchOntologyMetricInfo } from "@/lib/api";
import type { OntologyMetricInfoItem } from "@/lib/types";

// 온톨로지 재무 지표 설명 단일 출처 훅(B1). keys → {key: description} 매핑을 반환.
// 모듈 단위 캐시로 동일 keys 세트를 여러 컴포넌트가 써도 1회만 fetch.
// 로딩 중·실패 시 빈 매핑 → 호출측은 fallback 라벨/설명을 쓴다(회귀 방지).

type InfoMap = Record<string, OntologyMetricInfoItem>;

const _cache = new Map<string, InfoMap>(); // key: 정규화된 keys 문자열
const _pending = new Map<string, Promise<InfoMap>>();

function cacheKey(keys: string[]): string {
  return [...keys].sort().join(",");
}

function load(keys: string[]): Promise<InfoMap> {
  const ck = cacheKey(keys);
  const cached = _cache.get(ck);
  if (cached) {
    return Promise.resolve(cached);
  }
  const pending = _pending.get(ck);
  if (pending) {
    return pending;
  }
  const p = fetchOntologyMetricInfo(keys)
    .then((res) => {
      const map: InfoMap = {};
      for (const it of res.items) {
        map[it.key] = it;
      }
      _cache.set(ck, map);
      return map;
    })
    .catch(() => {
      // 실패 시 빈 매핑 캐싱(재시도 폭주 방지) — 호출측 fallback.
      const empty: InfoMap = {};
      _cache.set(ck, empty);
      return empty;
    })
    .finally(() => {
      _pending.delete(ck);
    });
  _pending.set(ck, p);
  return p;
}

export function useMetricInfo(keys: string[]): {
  info: InfoMap;
  loaded: boolean;
} {
  const [info, setInfo] = useState<InfoMap>(() => {
    const ck = cacheKey(keys);
    return _cache.get(ck) ?? {};
  });
  const [loaded, setLoaded] = useState<boolean>(() => _cache.has(cacheKey(keys)));

  useEffect(() => {
    let active = true;
    load(keys).then((m) => {
      if (active) {
        setInfo(m);
        setLoaded(true);
      }
    });
    return () => {
      active = false;
    };
    // keys 배열 참조가 매 렌더링 바뀔 수 있어 cacheKey 기준으로 의존.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey(keys)]);

  return { info, loaded };
}