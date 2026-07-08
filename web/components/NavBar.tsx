"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import styles from "./NavBar.module.css";

const LINKS = [
  { href: "/", label: "Today's Brew", featured: false },
  { href: "/screener", label: "스몰캡 스크리너", featured: true },
  { href: "/industries", label: "산업 흐름", featured: false },
  { href: "/companies", label: "기업 분석", featured: false },
] as const;

export default function NavBar() {
  const pathname = usePathname();

  return (
    <header className={styles.header}>
      <nav className={styles.nav}>
        <Link href="/" className={styles.brand}>
          <span className={styles.brandMark}>☕</span>
          <span>돈냥이 리서치</span>
        </Link>
        <ul className={styles.links}>
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
        </ul>
      </nav>
    </header>
  );
}
