// 연동 차트 동기화 브리지 — logical index ↔ calendar epoch(초) 상호변환.
//
// lightweight-charts 의 '시간 범위'(setVisibleRange/subscribeVisibleTimeRangeChange)는 데이터 경계
// (첫·마지막 봉)로 clamp 돼, 데이터 끝 너머 여백으로 줌아웃·이동한 구간을 표현하지 못한다. 그래서
// 마스터 차트를 여백으로 밀면 팔로워가 못 따라온다. 대신 '논리 범위'(logical range)는 데이터 밖 인덱스
// (음수·초과)를 허용하고 여백을 렌더한다 → 논리 범위로 동기화한다.
//
// 봉 밀도가 다른 차트(일봉 vs 분기)를 잇기 위해 각 차트의 봉 epoch 축을 기준으로 논리↔달력 변환한다.
// 공유 상태는 달력(ISO 날짜)이라 밀도가 달라도 같은 '기간'을 가리킨다.

import type { Time } from "lightweight-charts";

// lightweight-charts Time → calendar epoch(초). range prop 은 dateToTs 로 만든 숫자(epoch)지만
// 방어적으로 문자열·BusinessDay 도 처리한다.
export function timeToEpoch(t: Time): number {
  if (typeof t === "number") {
    return t;
  }
  if (typeof t === "string") {
    return Date.parse(`${t.slice(0, 10)}T00:00:00Z`) / 1000;
  }
  const { year, month, day } = t;
  return Date.parse(`${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}T00:00:00Z`) / 1000;
}

// 평균 봉 간격(초). 데이터 1개 이하면 하루로 폴백(0 나눗셈 방지).
function avgSpacing(epochs: number[]): number {
  const n = epochs.length;
  return n < 2 ? 86400 : (epochs[n - 1] - epochs[0]) / (n - 1);
}

// calendar epoch(초) → logical index. 데이터 밖이면 평균 간격으로 외삽(음수·초과 허용).
export function epochToLogical(epochs: number[], epoch: number): number {
  const n = epochs.length;
  if (n === 0) {
    return 0;
  }
  if (epoch <= epochs[0]) {
    return (epoch - epochs[0]) / avgSpacing(epochs);
  }
  if (epoch >= epochs[n - 1]) {
    return n - 1 + (epoch - epochs[n - 1]) / avgSpacing(epochs);
  }
  // 이진탐색으로 epochs[lo] <= epoch < epochs[hi=lo+1] 구간을 찾아 선형보간.
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (epochs[mid] <= epoch) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  const span = epochs[hi] - epochs[lo] || 1;
  return lo + (epoch - epochs[lo]) / span;
}

// logical index → calendar epoch(초). epochToLogical 의 역변환(데이터 밖은 외삽).
export function logicalToEpoch(epochs: number[], logical: number): number {
  const n = epochs.length;
  if (n === 0) {
    return 0;
  }
  if (logical <= 0) {
    return epochs[0] + logical * avgSpacing(epochs);
  }
  if (logical >= n - 1) {
    return epochs[n - 1] + (logical - (n - 1)) * avgSpacing(epochs);
  }
  const i = Math.floor(logical);
  return epochs[i] + (logical - i) * (epochs[i + 1] - epochs[i]);
}

// calendar epoch(초) → 'YYYY-MM-DD'. 공유 date-range(ISO) 로 내보낼 때 사용.
export function epochToIso(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}

// 논리 범위 {from,to}(밀도 무관 달력 좌표) → 이 차트 축 기준 논리 범위로 변환해 반환.
// 공유 구간(epoch)을 이 차트에 적용할 때 setVisibleLogicalRange 인자로 쓴다.
export function epochRangeToLogical(
  epochs: number[],
  fromEpoch: number,
  toEpoch: number,
): { from: number; to: number } {
  return {
    from: epochToLogical(epochs, fromEpoch),
    to: epochToLogical(epochs, toEpoch),
  };
}

// 이 차트의 논리 범위 → 공유 date-range(ISO from/to). 데이터 밖(여백)이면 외삽된 날짜가 나온다.
export function logicalRangeToIso(
  epochs: number[],
  from: number,
  to: number,
): { from: string; to: string } {
  return {
    from: epochToIso(logicalToEpoch(epochs, from)),
    to: epochToIso(logicalToEpoch(epochs, to)),
  };
}
