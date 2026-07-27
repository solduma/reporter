"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchOwnership } from "@/lib/api";
import type {
  DilutionRow,
  MajorHolderRow,
  OwnershipChangeRow,
  OwnershipResponse,
  OwnershipSummaryRow,
  ShareholderRow,
} from "@/lib/types";

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

function fmtWon(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(1)}억`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(0)}만`;
  return n.toLocaleString("ko-KR");
}

// 주주 명부를 현재 기준 스냅샷으로 정리 — 동일 주주는 한 줄로, 실제 지분이 있는 주주만 노출.
function snapshotShareholders(rows: ShareholderRow[]): ShareholderRow[] {
  const map = new Map<string, ShareholderRow>();
  for (const s of rows) {
    const existing = map.get(s.holder_name);
    // 동명이면 더 높은 지분율/최신 관계를 우선한다.
    if (!existing || (s.stake_pct ?? 0) > (existing.stake_pct ?? 0)) {
      map.set(s.holder_name, s);
    }
  }
  return Array.from(map.values())
    .filter((s) => (s.stake_pct ?? 0) > 0)
    .sort((a, b) => (b.stake_pct ?? 0) - (a.stake_pct ?? 0));
}

export default function OwnershipStructure({ code }: { code: string }) {
  const [state, setState] = useState<
    { status: "loading" | "ready" | "error"; data: OwnershipResponse | null; message?: string }
  >({ status: "loading", data: null });
  const [showAllSubs, setShowAllSubs] = useState(false);
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

  const {
    shareholders = [],
    subsidiaries = [],
    changes = [],
    as_of_year,
    changes_stale,
    summary = null,
    major_holders = [],
    dilution = [],
    subsidiary_total,
    subsidiary_filtered,
  } = state.data ?? {};
  const empty = shareholders.length === 0 && subsidiaries.length === 0 && changes.length === 0;
  if (empty) {
    return <p className={styles.hint}>지분구조 데이터가 없습니다.</p>;
  }

  // "전체 출자 보기" 토글 시 모든 자회사 표시.
  const filteredCount = subsidiary_filtered ?? subsidiaries.length;
  const totalCount = subsidiary_total ?? subsidiaries.length;
  const displaySubs = showAllSubs ? subsidiaries : subsidiaries.slice(0, filteredCount);
  const snapshotHolders = snapshotShareholders(shareholders);

  return (
    <div className={styles.wrap}>
      {/* 분석 배지 — 지배력·리스크·수급 */}
      {summary ? <SummaryBadges summary={summary} /> : null}

      <div className={styles.grid}>
        <OwnershipColumn
          title="주주에 관한 사항"
          asOfYear={as_of_year}
          emptyText="주주 데이터가 없습니다."
        >
          {snapshotHolders.length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.nameCol} scope="col">주주</th>
                  <th scope="col">관계</th>
                  <th scope="col">지분율</th>
                </tr>
              </thead>
              <tbody>
                {snapshotHolders.map((s) => (
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

          {/* 5%+ 대량보유주주 */}
          {major_holders.length > 0 && (
            <div className={styles.subSection}>
              <h4 className={styles.subSectionTitle}>5%+ 대량보유주주</h4>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.nameCol} scope="col">보고자</th>
                      <th scope="col">보유비율</th>
                      <th scope="col">보유주식</th>
                      <th scope="col">사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {major_holders.map((h, i) => (
                      <tr key={`mh-${i}`}>
                        <th className={styles.nameCol} scope="row">{h.repror}</th>
                        <td className={styles.num}>{fmtStake(h.stkrt)}</td>
                        <td className={styles.num}>{fmtShares(h.stkqy)}</td>
                        <td>{h.report_resn || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* CB/BW 희석 리스크 */}
          {dilution.length > 0 && (
            <div className={styles.subSection}>
              <h4 className={styles.subSectionTitle}>CB/BW 희석 리스크</h4>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th scope="col">종류</th>
                      <th scope="col">결의일</th>
                      <th scope="col">발행금액</th>
                      <th scope="col">전환가액</th>
                      <th scope="col">발행주식</th>
                      <th scope="col">비율</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dilution.map((d, i) => (
                      <tr key={`dil-${i}`}>
                        <td><span className={d.type === "CB" ? styles.tagCb : styles.tagBw}>{d.type}</span></td>
                        <td>{d.bddd || "—"}</td>
                        <td className={styles.num}>{fmtWon(d.bd_fta)}</td>
                        <td className={styles.num}>{d.cv_prc?.toLocaleString("ko-KR") ?? "—"}원</td>
                        <td className={styles.num}>{fmtShares(d.cvisstk_cnt)}</td>
                        <td className={styles.num}>{d.tisstk_vs !== null ? `${d.tisstk_vs}%` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className={styles.dilutionNote}>
                ※ 주식담보대출 정보는 공시 원문 참조
              </p>
            </div>
          )}
        </OwnershipColumn>

        <OwnershipColumn
          title={`자회사 · 출자사 (의미 ${filteredCount} / 전체 ${totalCount})`}
          emptyText="자회사 데이터가 없습니다."
        >
          {displaySubs.length > 0 && (
            <>
              <div className={styles.legend}>
                <span className={styles.tagSignificance}>이익10%+</span>
                <span className={styles.tagSignificance}>적자</span>
                <span className={styles.tagSignificance}>신사업</span>
              </div>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.nameCol} scope="col">관계사</th>
                      <th scope="col">관계</th>
                      <th scope="col">지분율</th>
                      <th scope="col">당기순이익</th>
                      <th scope="col">출자목적</th>
                      <th scope="col">비고</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displaySubs.map((s) => (
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
                        <td className={`${styles.num} ${(s.sub_net_profit ?? 0) < 0 ? styles.down : ""}`}>
                          {fmtWon(s.sub_net_profit)}
                        </td>
                        <td>{s.inv_purpose || "—"}</td>
                        <td>
                          {(s.significance ?? []).map((tag) => (
                            <span key={tag} className={styles.tagSignificance}>{tag}</span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {totalCount > filteredCount && (
                <button
                  className={styles.toggleBtn}
                  onClick={() => setShowAllSubs((v) => !v)}
                >
                  {showAllSubs ? "의미 자회사만 보기" : `전체 출자 보기 (${totalCount}개)`}
                </button>
              )}
            </>
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

function SummaryBadges({ summary }: { summary: OwnershipSummaryRow }) {
  return (
    <div className={styles.badges}>
      <div className={styles.badge}>
        <span className={styles.badgeLabel}>지배력</span>
        <span className={styles.badgeValue}>
          {summary.group_stake_pct !== null ? `${summary.group_stake_pct}%` : "—"}
        </span>
        <span className={`${styles.badgeClass} ${styles[`class_${summary.group_class}`] || ""}`}>
          {summary.group_class || "—"}
        </span>
      </div>
      <div className={styles.badge}>
        <span className={styles.badgeLabel}>수급</span>
        <span className={styles.badgeValue}>
          {summary.floating_ratio !== null ? `${summary.floating_ratio}%` : "—"}
        </span>
        <span className={`${styles.badgeClass} ${styles[`class_${summary.floating_class}`] || ""}`}>
          {summary.floating_class || "—"}
        </span>
      </div>
      <div className={styles.badge}>
        <span className={styles.badgeLabel}>희석</span>
        <span className={styles.badgeValue}>
          {summary.dilution_pct !== null ? `${summary.dilution_pct}%` : "—"}
        </span>
        <span className={styles.badgeClass}>
          {summary.dilution_pct !== null
            ? summary.dilution_pct > 10 ? "주의" : "양호"
            : "—"}
        </span>
      </div>
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
