"use client";

import { driver } from "driver.js";
import type { DriveStep } from "driver.js";

import "driver.js/dist/driver.css";

// 온보딩 투어 — driver.js 스포트라이트. 페이지별 스텝을 data-tour 앵커로 지정한다.
// 첫 방문 시 1회 자동 시작(localStorage), 이후 '가이드 다시 보기'로 수동 실행.

const SEEN_PREFIX = "reporter.tour.seen.";

export type TourId = "screener" | "company";

const STEPS: Record<TourId, DriveStep[]> = {
  screener: [
    {
      element: '[data-tour="strategy"]',
      popover: {
        title: "① 전략 고르기",
        description:
          "종합·성장·가치·추세·탑다운 중 관점을 고릅니다. 처음이면 '종합'으로 시작하세요 — 4축을 합친 테크노펀더멘탈 점수 순으로 봅니다.",
      },
    },
    {
      element: '[data-tour="filters"]',
      popover: {
        title: "② 조건 좁히기",
        description:
          "'펼치기'를 눌러 시가총액·성장률·모멘텀 등으로 후보를 좁힙니다. 기본값만으로도 시작할 수 있고, 걸어둔 조건 수는 배지로 표시됩니다.",
      },
    },
    {
      element: '[data-tour="results"]',
      popover: {
        title: "③ 후보 보기",
        description:
          "조건을 통과한 종목이 스코어 순으로 나옵니다. 헤더를 눌러 정렬할 수 있습니다.",
      },
    },
    {
      element: '[data-tour="firstRow"]',
      popover: {
        title: "④ 종목 분석으로",
        description:
          "행을 누르면 그 종목의 상세 분석으로 이동합니다. 거기서 성장·기술·밸류를 한 번에 확인하세요.",
      },
    },
  ],
  company: [
    {
      element: '[data-tour="snapshot"]',
      popover: {
        title: "① 스냅샷",
        description: "시총·현재가·매출 성장률 등 종목의 기본 숫자를 먼저 봅니다.",
      },
    },
    {
      element: '[data-tour="analysis"]',
      popover: {
        title: "② 종합 분석",
        description:
          "성장·기술·탑다운 3축을 0~100 점수로. 60↑이면 양호, 40↓이면 약함(같은 후보군 내 상대 점수).",
      },
    },
    {
      element: '[data-tour="valuation"]',
      popover: {
        title: "③ 밸류에이션",
        description:
          "PER/PBR/PSR 이 과거 대비 싼지 비싼지. 하단(25%) 근처면 역사적 저평가 위치입니다.",
      },
    },
  ],
};

function seenKey(id: TourId): string {
  return `${SEEN_PREFIX}${id}`;
}

export function hasSeenTour(id: TourId): boolean {
  if (typeof window === "undefined") {
    return true; // SSR: 자동 시작 안 함
  }
  return window.localStorage.getItem(seenKey(id)) === "1";
}

export function markTourSeen(id: TourId): void {
  window.localStorage.setItem(seenKey(id), "1");
}

// 투어 실행. 대상 요소가 아직 없으면(로딩 중) 존재하는 스텝만 돌린다.
export function startTour(id: TourId): void {
  const steps = STEPS[id].filter((s) => {
    const sel = typeof s.element === "string" ? s.element : null;
    return sel ? document.querySelector(sel) !== null : true;
  });
  if (steps.length === 0) {
    return;
  }
  const d = driver({
    showProgress: true,
    nextBtnText: "다음",
    prevBtnText: "이전",
    doneBtnText: "완료",
    steps,
    onDestroyed: () => markTourSeen(id),
  });
  d.drive();
}
