import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { AUTH_COOKIE, verifyToken } from "@/lib/auth";

// LOGIN_PASSWORD 미설정 시엔 게이트를 열어 둔다(로컬 개발 편의).
const PASSWORD = process.env.LOGIN_PASSWORD;

export async function middleware(req: NextRequest) {
  if (!PASSWORD) {
    return NextResponse.next();
  }

  const token = req.cookies.get(AUTH_COOKIE)?.value;
  if (await verifyToken(token, PASSWORD)) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", req.url);
  // 로그인 후 원래 가려던 경로로 돌려보내기 위해 next 파라미터로 전달.
  loginUrl.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(loginUrl);
}

// 로그인 화면·인증 API·Next 내부 자원·정적 파일·파비콘은 게이트에서 제외한다.
// icon.svg/apple-icon 은 App Router 가 만드는 파비콘 라우트라, 로그인 전에도 브라우저가
// 쿠키 없이 요청하므로 게이트에서 빼야 탭 아이콘이 보인다.
// share 는 딥다이브 결과의 무인증 임시 공유 페이지(token 기반, 30분 TTL) — 로그인 없이 접근.
// share 페이지가 호출하는 조회 API(/api/deepdive/share/{token})도 게이트 밖이어야 한다.
export const config = {
  matcher: [
    "/((?!login|share|api/login|api/logout|api/deepdive/share|_next/static|_next/image|favicon.ico|icon.svg|apple-icon).*)",
  ],
};
