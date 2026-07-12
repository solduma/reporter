"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useStopAlertCount } from "@/lib/useStopAlert";

import styles from "./NavBar.module.css";

const LINKS = [
  { href: "/", label: "Today's Brew", featured: false },
  { href: "/screener", label: "스몰캡 스크리너", featured: true },
  { href: "/us-screener", label: "US 스크리너", featured: false },
  { href: "/industries", label: "산업 흐름", featured: false },
  { href: "/companies", label: "기업 분석", featured: false },
  { href: "/portfolio", label: "내 보유종목", featured: false },
  { href: "/archive", label: "브리핑 아카이브", featured: false },
] as const;

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const stopAlerts = useStopAlertCount();

  // 경로가 바뀌면(다른 페이지 이동) 모바일 메뉴를 닫는다.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // 열린 동안 바깥 클릭·Escape 로도 닫는다(같은 페이지 링크 탭 등 pathname 불변 케이스 보완).
  useEffect(() => {
    if (!open) {
      return;
    }
    const onDown = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

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
      <nav className={styles.nav} ref={navRef}>
        <Link href="/" className={styles.brand} onClick={() => setOpen(false)}>
          <span className={styles.brandMark}>☕</span>
          <span>Report Pulse</span>
        </Link>

        {/* 모바일 토글: 데스크톱에선 CSS 로 숨김 */}
        <button
          type="button"
          className={styles.menuToggle}
          aria-label={open ? "메뉴 닫기" : "메뉴 열기"}
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
                <Link
                  href={link.href}
                  className={classes.join(" ")}
                  onClick={() => setOpen(false)}
                >
                  {link.label}
                  {link.href === "/portfolio" && stopAlerts > 0 ? (
                    <span className={styles.alertBadge} aria-label={`손절 경보 ${stopAlerts}건`}>
                      {stopAlerts}
                    </span>
                  ) : null}
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
