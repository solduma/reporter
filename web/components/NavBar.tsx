"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import styles from "./NavBar.module.css";

type NavLeaf = {
  href: string;
  label: string;
};

type NavGroup = {
  label: string;
  children: NavLeaf[];
};

type NavEntry = NavLeaf | NavGroup;

const NAV: NavEntry[] = [
  { href: "/", label: "Today's Brew" },
  {
    label: "종목",
    children: [
      { href: "/screener", label: "국내 스크리너" },
      { href: "/us-screener", label: "US 스크리너" },
      { href: "/companies", label: "기업 분석" },
      { href: "/ir-interview", label: "주담 전략" },
    ],
  },
  {
    label: "산업·온톨로지",
    children: [
      { href: "/industries", label: "산업 흐름" },
      { href: "/ontology", label: "비즈니스 온톨로지" },
      { href: "/ontology/review", label: "온톨로지 검수" },
    ],
  },
  { href: "/calendar", label: "경제 캘린더" },
  { href: "/archive", label: "브리핑 아카이브" },
];

function isGroup(entry: NavEntry): entry is NavGroup {
  return "children" in entry;
}

function isActiveHref(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

function isGroupActive(group: NavGroup, pathname: string): boolean {
  return group.children.some((child) => isActiveHref(child.href, pathname));
}

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();

  // 모바일 전체 메뉴 토글
  const [open, setOpen] = useState(false);
  // 데스크톱 드롭다운 그룹 (열린 그룹 라벨)
  const [openGroup, setOpenGroup] = useState<string | null>(null);

  const navRef = useRef<HTMLElement>(null);

  // 경로가 바뀌면 모바일 메뉴를 닫는다.
  useEffect(() => {
    setOpen(false);
    setOpenGroup(null);
  }, [pathname]);

  // 열린 동안 바깥 클릭·Escape 로 닫는다.
  useEffect(() => {
    if (!open && openGroup === null) {
      return;
    }
    const onDown = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpen(false);
        setOpenGroup(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setOpenGroup(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, openGroup]);

  const handleGroupToggle = useCallback((label: string) => {
    setOpenGroup((prev) => (prev === label ? null : label));
  }, []);

  const handleChildClick = useCallback(() => {
    setOpenGroup(null);
    setOpen(false);
  }, []);

  // 로그인 화면·무인증 공유 페이지에는 내비게이션을 노출하지 않는다.
  if (pathname === "/login" || pathname.startsWith("/share/")) {
    return null;
  }

  async function handleLogout() {
    setOpen(false);
    setOpenGroup(null);
    await fetch("/api/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className={styles.header}>
      <nav className={styles.nav} ref={navRef}>
        <Link href="/" className={styles.brand} onClick={() => { setOpen(false); setOpenGroup(null); }}>
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

        <ul className={open ? `${styles.links} ${styles.open}` : styles.links}>
          {NAV.map((entry) => {
            if (isGroup(entry)) {
              const groupActive = isGroupActive(entry, pathname);
              const groupOpen = openGroup === entry.label;
              return (
                <li key={entry.label} className={styles.group}>
                  <button
                    type="button"
                    className={`${styles.groupToggle}${groupActive ? ` ${styles.active}` : ""}`}
                    onClick={() => handleGroupToggle(entry.label)}
                    aria-expanded={groupOpen}
                  >
                    {entry.label}
                  </button>
                  {groupOpen && (
                    <ul className={styles.dropdown}>
                      {entry.children.map((child) => (
                        <li key={child.href}>
                          <Link
                            href={child.href}
                            className={`${styles.dropdownItem}${isActiveHref(child.href, pathname) ? ` ${styles.active}` : ""}`}
                            onClick={handleChildClick}
                          >
                            {child.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            }

            // 단일 항목
            const active = isActiveHref(entry.href, pathname);
            return (
              <li key={entry.href}>
                <Link
                  href={entry.href}
                  className={`${styles.link}${active ? ` ${styles.active}` : ""}`}
                  onClick={() => { setOpen(false); setOpenGroup(null); }}
                >
                  {entry.label}
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
