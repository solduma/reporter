import { NextResponse } from "next/server";

import { AUTH_COOKIE, signToken } from "@/lib/auth";

const PASSWORD = process.env.LOGIN_PASSWORD;
const MAX_AGE = 60 * 60 * 24 * 30; // 30일
// 로그인 서버 핸들러는 same-origin rewrite 대상이 아니라 API 를 직접 호출한다.
const API_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8010";

// 로그인 직후 지수 시세·일봉 캐시를 백그라운드로 데운다(대시보드 첫 로드의 외부 왕복 제거).
// fire-and-forget: 실패해도 로그인엔 영향 없음.
function warmCaches(): void {
  void fetch(`${API_TARGET}/api/warm`, { method: "POST" }).catch(() => {});
}

export async function POST(req: Request) {
  if (!PASSWORD) {
    // 비밀번호 미설정이면 게이트가 열려 있으므로 로그인도 불필요.
    return NextResponse.json({ ok: true });
  }

  let password: unknown;
  try {
    ({ password } = await req.json());
  } catch {
    return NextResponse.json({ ok: false, error: "잘못된 요청입니다." }, { status: 400 });
  }

  if (typeof password !== "string" || password !== PASSWORD) {
    return NextResponse.json({ ok: false, error: "비밀번호가 올바르지 않습니다." }, { status: 401 });
  }

  warmCaches();
  const res = NextResponse.json({ ok: true });
  // secure 는 생략 — 배포가 HTTP(localhost:43000, TLS 없음)라 secure 면 쿠키가 안 실린다.
  res.cookies.set(AUTH_COOKIE, await signToken(PASSWORD), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE,
  });
  return res;
}
