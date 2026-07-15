import type { IChartApi, LogicalRange } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { ChartRange } from "@/components/CandleChart";
import {
  epochRangeToLogical,
  logicalRangeToIso,
  timeToEpoch,
} from "@/lib/chartSync";

// 연동 차트 공유 동기화 훅 — 여러 차트를 하나의 date-range 로 묶는다.
//
// 논리 범위(logical range)로 동기화한다: 시간 범위와 달리 데이터 경계 밖(여백) 인덱스를 허용해,
// 마스터를 데이터 끝 너머로 줌아웃·이동해도 그 구간이 팔로워에 그대로 전파된다. 밀도가 다른 차트
// (일봉 vs 분기)는 각자의 봉 epoch 축으로 논리↔달력 변환해 같은 '기간'을 가리키게 한다.
//
// 프로그램적 setVisibleLogicalRange 가 유발하는 되먹임을 억제창(SUPPRESS_MS)으로 삼키고, 드래그 중
// 쏟아지는 이벤트는 rAF 로 프레임당 1회만 상위에 보고한다. 자기가 방금 내보낸 구간이 range 로 되돌아
// 오면 재적용을 건너뛴다(자기 메아리).
//
// - getChart: 차트 인스턴스 획득자(마운트 effect 에서 chartRef 를 채운 뒤 그 ref 반환).
// - getEpochs: 이 차트의 봉 epoch(초) 오름차순 배열 획득자(데이터 바뀔 때마다 최신값).
// - range: 공유 표시 구간(달력 epoch 기반 ChartRange). 없으면 fitContent.
// - onRangeChange: 사용자 조작 시 공유 date-range(ISO)를 갱신하는 콜백.
// - deps: 차트가 재생성되는 조건(데이터·타임프레임 등). 바뀌면 초기 구간을 다시 적용한다.
const SUPPRESS_MS = 250;

interface SyncArgs {
  getChart: () => IChartApi | null;
  getEpochs: () => number[];
  range: ChartRange | null;
  onRangeChange?: (from: string, to: string) => void;
  deps: unknown[];
}

export function useChartRangeSync({
  getChart,
  getEpochs,
  range,
  onRangeChange,
  deps,
}: SyncArgs): void {
  const suppressUntilRef = useRef(0);
  const emitRafRef = useRef(0);
  const pendingRef = useRef<{ from: string; to: string } | null>(null);
  const lastEmittedRef = useRef<{ from: string; to: string } | null>(null);
  // 최신 값을 effect 밖에서 읽기 위한 ref(콜백·range 는 매 렌더 바뀔 수 있음).
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const getEpochsRef = useRef(getEpochs);
  getEpochsRef.current = getEpochs;

  // 구독 + 초기 구간 적용. deps(데이터 등)로 차트가 재생성되면 다시 건다.
  useEffect(() => {
    const chart = getChart();
    if (!chart) {
      return;
    }
    const epochs = getEpochsRef.current();

    // 초기 구간: range 있으면 논리 범위로 적용(억제창), 없으면 전체.
    const applyInitial = () => {
      const r = rangeRef.current;
      if (r && epochs.length > 0) {
        suppressUntilRef.current = Date.now() + SUPPRESS_MS;
        const lr = epochRangeToLogical(epochs, timeToEpoch(r.from), timeToEpoch(r.to));
        try {
          chart.timeScale().setVisibleLogicalRange(lr as LogicalRange);
          return;
        } catch {
          /* 폴백 아래 */
        }
      }
      chart.timeScale().fitContent();
    };
    applyInitial();

    const onLogicalRangeChange = (lr: LogicalRange | null) => {
      if (Date.now() < suppressUntilRef.current || !lr || !onRangeChangeRef.current) {
        return;
      }
      const eps = getEpochsRef.current();
      if (eps.length === 0) {
        return;
      }
      pendingRef.current = logicalRangeToIso(eps, lr.from, lr.to);
      if (emitRafRef.current) {
        return;
      }
      emitRafRef.current = requestAnimationFrame(() => {
        emitRafRef.current = 0;
        const p = pendingRef.current;
        if (p && onRangeChangeRef.current) {
          lastEmittedRef.current = p;
          onRangeChangeRef.current(p.from, p.to);
        }
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onLogicalRangeChange);

    return () => {
      if (emitRafRef.current) {
        cancelAnimationFrame(emitRafRef.current);
        emitRafRef.current = 0;
      }
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onLogicalRangeChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // range 변경만 반영(차트 재생성 없이). 자기가 방금 내보낸 구간이면 이미 그 위치라 skip.
  useEffect(() => {
    const chart = getChart();
    if (!chart || !range) {
      return;
    }
    const epochs = getEpochsRef.current();
    if (epochs.length === 0) {
      return;
    }
    const lr = epochRangeToLogical(epochs, timeToEpoch(range.from), timeToEpoch(range.to));
    // 자기 메아리 판정: 이 차트가 방금 내보낸 구간이 그대로 되돌아온 것이면 이미 그 위치라 재적용 skip.
    const iso = logicalRangeToIso(epochs, lr.from, lr.to);
    const last = lastEmittedRef.current;
    if (last && last.from === iso.from && last.to === iso.to) {
      return;
    }
    suppressUntilRef.current = Date.now() + SUPPRESS_MS;
    try {
      chart.timeScale().setVisibleLogicalRange(lr as LogicalRange);
    } catch {
      /* 범위가 데이터 밖이면 무시 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);
}
