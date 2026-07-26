"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchOwnership } from "@/lib/api";
import type { OwnershipResponse, OwnershipChangeRow } from "@/lib/types";

import styles from "./OwnershipStructure.module.css";

// changes_stale 일 때 live 재조회 폴링 간격(쿼터초과·일시 실패 후 짧게 재시도).
const STALE_POLL_MS = 5000;

// relate 라벨 축약 — DART 원문(최대주주 본인/최대주주의 특수관계인 ...)을 표에 간결하게.
function relateLabel(relate: string): string {
  if (!relate) return "";
  if (relate === "최대주주 본인") return "최대주주";
  if (relate.startsWith("최대주주의 ")) return relate.slice("최대주주의 ".length);
  return relate;
}

function fmtStake(pct: number | null): string {
  return pct === null || pct === undefined ? "—" : `${pct.toFixed(2)}%`;
}

function fmtShares(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("ko-KR");
}

export default function OwnershipStructure({ code }: { code: string }) {
  const [state, setState] = useState<
    { status: "loading" | "ready" | "error"; data: OwnershipResponse | null; message?: string }
  >({ status: "loading", data: null });
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchOwnership(code);
      setState({ status: "ready", data: res });
    } catch (e) {
      setState({
        status: "error",
        data: null,
        message: e instanceof Error ? e.message : "지분구조를 불러오지 못했습니다",
      });
    }
  }, [code]);

  useEffect(() => {
    void load();
  }, [load]);

  // changes_stale(쿼터초과·키없음 등으로 live 조회 못 함) → 짧은 폴링으로 재시도.
  useEffect(() => {
    if (state.status === "ready" && state.data?.changes_stale) {
      pollRef.current = setTimeout(() => void load(), STALE_POLL_MS);
    }
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [state, load]);

  if (state.status === "loading") {
    return <p className={styles.hint}>지분구조를 불러오는 중…</p>;
  }
  if (state.status === "error" || !state.data) {
    return <p className={styles.hint}>{state.message ?? "지분구조 데이터가 없습니다."}</p>;
  }

  const { shareholders, subsidiaries, changes, as_of_year, changes_stale } = state.data;
  const empty = shareholders.length === 0 && subsidiaries.length === 0 && changes.length === 0;
  if (empty) {
    return <p className={styles.hint}>지분구조 데이터가 없습니다.</p>;
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.grid}>
        <OwnershipColumn
          title="주주에 관한 사항"
          asOfYear={as_of_year}
          emptyText="주주 데이터가 없습니다."
        >
          {shareholders.length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.nameCol} scope="col">주주</th>
                  <th scope="col">관계</th>
                  <th scope="col">지분율</th>
                </tr>
              </thead>
              <tbody>
                {shareholders.map((s) => (
                  <tr key={s.holder_name}>
                    <th className={styles.nameCol} scope="row">
                      {s.related_stock_code ? (
                        <Link href={`/companies/${s.related_stock_code}`} className={styles.link}>
                          {s.holder_name}
                        </Link>
                      ) : (
                        <span className={s.is_corporate ? styles.corp : styles.person}>
                          {s.holder_name}
                        </span>
                      )}
                    </th>
                    <td>{relateLabel(s.relate)}</td>
                    <td className={styles.num}>{fmtStake(s.stake_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </OwnershipColumn>

        <OwnershipColumn title="자회사 · 출자사" emptyText="자회사 데이터가 없습니다.">
          {subsidiaries.length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.nameCol} scope="col">관계사</th>
                  <th scope="col">관계</th>
                  <th scope="col">지분율</th>
                </tr>
              </thead>
              <tbody>
                {subsidiaries.map((s) => (
                  <tr key={`${s.related_name}-${s.relation}`}>
                    <th className={styles.nameCol} scope="row">
                      {s.related_stock_code ? (
                        <Link href={`/companies/${s.related_stock_code}`} className={styles.link}>
                          {s.related_name}
                        </Link>
                      ) : (
                        <span className={styles.corp}>{s.related_name}</span>
                      )}
                    </th>
                    <td>{s.relation === "subsidiary" ? "자회사" : "출자사"}</td>
                    <td className={styles.num}>{fmtStake(s.stake_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </OwnershipColumn>
      </div>

      {changes.length > 0 && (
        <div className={styles.changes}>
          <div className={styles.changesHead}>
            <h3 className={styles.subTitle}>최근 지분 변동</h3>
            {changes_stale ? <span className={styles.staleTag}>갱신 중</span> : null}
          </div>
          <div className={styles.scroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">일자</th>
                  <th className={styles.nameCol} scope="col">보고자</th>
                  <th scope="col">직위</th>
                  <th scope="col">증감</th>
                  <th scope="col">변동후</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <ChangeRow key={c.rcept_no} change={c} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function OwnershipColumn({
  title,
  asOfYear,
  emptyText,
  children,
}: {
  title: string;
  asOfYear?: number | null;
  emptyText: string;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.column}>
      <div className={styles.colHead}>
        <h3 className={styles.subTitle}>{title}</h3>
        {asOfYear ? (
          <span className={styles.asOfTag}>기준: {asOfYear} 사업보고서</span>
        ) : null}
      </div>
      {children ? children : <p className={styles.hint}>{emptyText}</p>}
    </div>
  );
}

function ChangeRow({ change }: { change: OwnershipChangeRow }) {
  const delta = change.shares_delta;
  const positive = delta !== null && delta >= 0;
  return (
    <tr>
      <td>{change.rcept_date ?? "—"}</td>
      <th className={styles.nameCol} scope="row">
        {change.reporter}
      </th>
      <td>{change.position || "—"}</td>
      <td className={`${styles.num} ${positive ? styles.up : styles.down}`}>
        {delta === null ? "—" : `${positive ? "+" : ""}${delta.toLocaleString("ko-KR")}주`}
      </td>
      <td className={styles.num}>{fmtShares(change.shares_after)}</td>
    </tr>
  );
}
