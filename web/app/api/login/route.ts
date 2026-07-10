import { NextResponse } from "next/server";

import { AUTH_COOKIE, signToken } from "@/lib/auth";

const PASSWORD = process.env.LOGIN_PASSWORD;
const MAX_AGE = 60 * 60 * 24 * 30; // 30일

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
