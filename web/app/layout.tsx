import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import NavBar from "@/components/NavBar";
import QueryProvider from "@/lib/query-client";

import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://report.il-jo.com"),
  title: {
    default: "Today's Brew · 오늘의 증권 리서치",
    template: "%s · Today's Brew",
  },
  description: "매일 아침 증권사 리포트를 수집·분석한 오늘의 시황과 리포트 브리핑 — 산업/종목 분석, 리서치 브리핑, 딥다이브까지.",
  openGraph: {
    title: "Today's Brew · 오늘의 증권 리서치",
    description: "매일 아침 증권사 리포트를 수집·분석한 오늘의 시황과 리포트 브리핑",
    url: "https://report.il-jo.com",
    siteName: "Today's Brew",
    locale: "ko_KR",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Today's Brew · 오늘의 증권 리서치",
    description: "매일 아침 증권사 리포트를 수집·분석한 오늘의 시황과 리포트 브리핑",
  },
};

// 모바일이 데스크톱 폭으로 렌더 후 축소되지 않도록 기기 폭에 맞춘다(반응형의 전제).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <QueryProvider>
          <NavBar />
          <main>{children}</main>
        </QueryProvider>
      </body>
    </html>
  );
}
