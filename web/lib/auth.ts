// 단일 비밀번호(LOGIN_PASSWORD) 기반 웹 접근 게이팅.
// 쿠키에는 비밀번호 자체가 아니라 HMAC 서명을 담는다 — 서버가 env 비밀번호로 재계산해
// 일치할 때만 통과시키므로, 비밀번호를 모르면 유효한 쿠키를 위조할 수 없다.
// Web Crypto(subtle)만 사용해 Edge middleware 와 Route Handler 양쪽에서 동작한다.

export const AUTH_COOKIE = "rp_auth";
const SIGN_PAYLOAD = "report-pulse-authenticated"; // 서명 대상 고정 문자열

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// LOGIN_PASSWORD 를 키로 고정 payload 를 HMAC-SHA256 서명한 hex 문자열.
export async function signToken(password: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(SIGN_PAYLOAD));
  return toHex(sig);
}

// 상수 시간 비교 — 타이밍 공격으로 서명을 한 바이트씩 알아내지 못하게 한다.
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

// 쿠키 토큰이 현재 LOGIN_PASSWORD 로 만든 서명과 일치하는지 검증한다.
export async function verifyToken(token: string | undefined, password: string): Promise<boolean> {
  if (!token || !password) {
    return false;
  }
  return timingSafeEqual(token, await signToken(password));
}
