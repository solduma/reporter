"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import styles from "./NavBar.module.css";

const LINKS = [
  { href: "/", label: "Today's Brew", featured: false },
  { href: "/screener", label: "스몰캡 스크리너", featured: true },
  { href: "/industries", label: "산업 흐름", featured: false },
  { href: "/companies", label: "기업 분석", featured: false },
  { href: "/archive", label: "브리핑 아카이브", featured: false },
] as const;

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  // 경로가 바뀌면(링크 이동) 모바일 메뉴를 닫는다.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // 로그인 화면에는 내비게이션을 노출하지 않는다.
  if (pathname === "/login") {
    return null;
  }

  async function handleLogout() {
    setOpen(false);
    await fetch("/api/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className={styles.header}>
      <nav className={styles.nav}>
        <Link href="/" className={styles.brand}>
          <span className={styles.brandMark}>☕</span>
          <span>Report Pulse</span>
        </Link>

        {/* 모바일 토글: 데스크톱에선 CSS 로 숨김 */}
        <button
          type="button"
          className={styles.menuToggle}
          aria-label="메뉴 열기"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "✕" : "☰"}
        </button>

        {/* 모바일에선 open 일 때만 펼침(드롭다운), 데스크톱에선 항상 가로 배치 */}
        <ul className={open ? `${styles.links} ${styles.open}` : styles.links}>
          {LINKS.map((link) => {
            const active = pathname === link.href;
            const classes = [styles.link];
            if (link.featured) {
              classes.push(styles.featured);
            }
            if (active) {
              classes.push(styles.active);
            }
            return (
              <li key={link.href}>
                <Link href={link.href} className={classes.join(" ")}>
                  {link.label}
                </Link>
              </li>
            );
          })}
          <li>
            <button type="button" className={styles.logout} onClick={handleLogout}>
              로그아웃
            </button>
          </li>
        </ul>
      </nav>
    </header>
  );
}
