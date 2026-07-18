"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchIrInterviewList } from "@/lib/api";
import type { IrInterviewListItem } from "@/lib/types";

import styles from "./page.module.css";

type State = { status: "loading" | "ready" | "error"; data: IrInterviewListItem[]; message?: string };

export default function IrInterviewListPage() {
  const [state, setState] = useState<State>({ status: "loading", data: [] });

  useEffect(() => {
    let active = true;
    fetchIrInterviewList()
      .then((data) => {
        if (active) setState({ status: "ready", data });
      })
      .catch((e: unknown) => {
        if (active)
          setState({
            status: "error",
            data: [],
            message: e instanceof Error ? e.message : "목록을 불러오지 못했습니다",
          });
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>주담(IR) 인터뷰 전략</h1>
      <p className={styles.sub}>
        딥다이브 밸류에이션의 불확실 가정을 겨냥한 주담 인터뷰 질문 — 종목별로 생성됩니다.
      </p>

      {state.status === "loading" ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : state.status === "error" ? (
        <p className={styles.error}>API 연결 실패: {state.message}</p>
      ) : state.data.length === 0 ? (
        <p className={styles.status}>
          생성된 주담 전략이 없습니다. 종목 딥다이브 완료 후 종목 페이지에서 생성하세요.
        </p>
      ) : (
        <ul className={styles.list}>
          {state.data.map((it) => (
            <li key={it.stock_code}>
              <Link href={`/ir-interview/${it.stock_code}`} className={styles.card}>
                <span className={styles.name}>{it.stock_name ?? it.stock_code}</span>
                <span className={styles.code}>{it.stock_code}</span>
                <span className={styles.meta}>질문 {it.total_questions}개</span>
                {it.as_of ? (
                  <span className={styles.asOf}>{it.as_of.slice(0, 10)}</span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
