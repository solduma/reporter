"use client";

// 자주 찾는 종목 — 브라우저 localStorage 에만 저장하는 웹 전용 목록.
// 상세 화면에서 조회한 종목이 자동 추가되고, 사용자가 카드에서 제거할 수 있다.
// 제거는 이 목록(웹 오브젝트)만 지우며 서버 데이터에는 영향이 없다.

import { useEffect, useState } from "react";

export interface QuickPick {
  code: string;
  name: string;
}

const KEY = "reporter.quickPicks.v1";
const MAX = 24; // 목록 상한(오래된 항목부터 밀려남)
// 같은 탭 내 반응성: storage 이벤트는 다른 탭에서만 발화하므로 커스텀 이벤트로 보완한다.
const CHANGE_EVENT = "reporter:quickpicks";

// 최초 방문 시 시드할 기본 종목(비어 있으면 안내가 허전하지 않도록). 이후 사용자 관리.
const DEFAULTS: QuickPick[] = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
  { code: "035420", name: "NAVER" },
  { code: "035720", name: "카카오" },
  { code: "005380", name: "현대차" },
  { code: "051910", name: "LG화학" },
];

function isPick(v: unknown): v is QuickPick {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as QuickPick).code === "string" &&
    typeof (v as QuickPick).name === "string"
  );
}

function read(): QuickPick[] {
  if (typeof window === "undefined") {
    return [];
  }
  const raw = window.localStorage.getItem(KEY);
  if (raw === null) {
    // 최초 1회: 기본 종목으로 시드하고 저장한다(초기화 여부는 키 존재로 판별).
    window.localStorage.setItem(KEY, JSON.stringify(DEFAULTS));
    return [...DEFAULTS];
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isPick) : [];
  } catch {
    return [];
  }
}

function write(list: QuickPick[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(KEY, JSON.stringify(list));
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function getQuickPicks(): QuickPick[] {
  return read();
}

// 조회한 종목을 목록 맨 앞에 추가한다(코드 기준 중복 제거·이름 최신화, 상한 초과분 절삭).
export function addQuickPick(pick: QuickPick): void {
  const rest = read().filter((p) => p.code !== pick.code);
  write([pick, ...rest].slice(0, MAX));
}

export function removeQuickPick(code: string): void {
  write(read().filter((p) => p.code !== code));
}

// companies 화면용 훅 — 마운트 시 읽고, 변경 이벤트(같은 탭·다른 탭)에 반응한다.
// ready 는 localStorage 를 실제로 읽었는지(effect 실행 후) 여부 — 로드 전 '빈 목록' 안내
// 깜빡임을 막기 위해 '아직 안 읽음'과 '진짜 비었음'을 구분한다.
export function useQuickPicks(): { picks: QuickPick[]; ready: boolean } {
  const [picks, setPicks] = useState<QuickPick[]>([]);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const sync = () => {
      setPicks(getQuickPicks());
      setReady(true);
    };
    sync();
    window.addEventListener(CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return { picks, ready };
}
