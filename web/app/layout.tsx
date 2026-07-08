import type { Metadata } from "next";
import type { ReactNode } from "react";

import NavBar from "@/components/NavBar";

import "./globals.css";

export const metadata: Metadata = {
  title: "Today's Brew · 오늘의 증권 리서치",
  description: "매일 아침 증권사 리포트를 수집·분석한 오늘의 시황과 리포트 브리핑",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <NavBar />
        <main>{children}</main>
      </body>
    </html>
  );
}
