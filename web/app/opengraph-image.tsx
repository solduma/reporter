import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "Today's Brew — 오늘의 증권 리서치";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OGImage() {
  return new ImageResponse(
    (
      // eslint-disable-next-line @next/next/no-img-element
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#faf8f5",
          padding: 56,
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          {/* 컵 아이콘 — icon.svg 모티브 */}
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 14,
              background: "#f0e6dd",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 32,
            }}
          >
            ☕
          </div>
          <span style={{ fontSize: 22, color: "#7b4b2a", letterSpacing: 2, fontWeight: 700 }}>
            TODAY&apos;S BREW
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ fontSize: 54, fontWeight: 800, color: "#1a1a1a", lineHeight: 1.15 }}>
            오늘의 증권 리서치
          </div>
          <div style={{ fontSize: 22, color: "#5c636e", lineHeight: 1.5 }}>
            매일 아침 증권사 리포트를 수집·분석한 시황과 리포트 브리핑
          </div>
        </div>

        <div style={{ fontSize: 18, color: "#9aa0a8" }}>report.il-jo.com</div>
      </div>
    ),
    { ...size },
  );
}
