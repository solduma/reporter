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

// 로그인 화면·인증 API·Next 내부 자원·정적 파일은 게이트에서 제외한다.
export const config = {
  matcher: ["/((?!login|api/login|api/logout|_next/static|_next/image|favicon.ico).*)"],
};
