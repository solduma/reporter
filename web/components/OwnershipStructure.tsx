"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

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

// YYYYMMDD → YYYY.MM.DD (DART 접수일자 표기).
function fmtDate(d: string): string {
  if (!d || d.length !== 8) return d || "—";
  return `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6, 8)}`;
}

// 5%+ 대량보유주주: 보고자별로 그룹화 — 동일 주주의 다수 보고서를
// 최신 보고(현재 기준) 1줄 + 변경 이력 1줄 요약으로 정리.
function groupMajorHolders(rows: MajorHolderRow[]): {
  repror: string;
  stkrt: number | null;
  stkqy: number | null;
  history: string;
  isContractOnly: boolean; // 최신 보고서 보유 전액이 주요체결(주식매매계약 체결, 이전 미완료)
}[] {
  const groups = new Map<string, MajorHolderRow[]>();
  for (const r of rows) {
    const arr = groups.get(r.repror) ?? [];
    arr.push(r);
    groups.set(r.repror, arr);
  }
  const out: {
    repror: string;
    stkrt: number | null;
    stkqy: number | null;
    history: string;
    isContractOnly: boolean;
  }[] = [];
  for (const [repror, arr] of groups) {
    const sorted = arr
      .slice()
      .sort((a, b) => (a.rcept_dt ?? "").localeCompare(b.rcept_dt ?? ""));
    const latest = sorted[sorted.length - 1];
    const first = sorted[0];
    let history: string;
    if (sorted.length <= 1) {
      history = `단일 보고 (${fmtDate(latest.rcept_dt)})`;
    } else {
      const f = first.stkrt !== null ? `${first.stkrt.toFixed(2)}%` : "—";
      const l = latest.stkrt !== null ? `${latest.stkrt.toFixed(2)}%` : "—";
      history = `${sorted.length}건 · ${f}→${l} (${fmtDate(latest.rcept_dt)})`;
    }
    // 보고된 보유 전액이 주요체결이면 이전(명의개서)이 아직 안 된 계약 분.
    const isContractOnly =
      (latest.ctr_stkqy ?? 0) > 0 && (latest.ctr_stkqy ?? 0) >= (latest.stkqy ?? 0);
    out.push({ repror, stkrt: latest.stkrt, stkqy: latest.stkqy, history, isContractOnly });
  }
  return out.sort((a, b) => (b.stkrt ?? 0) - (a.stkrt ?? 0));
}

// 1년 내 0%로 전환된 주주를 찾기 위한 윈도우(밀리초).
const ZERO_TRANSITION_WINDOW_MS = 365 * 24 * 60 * 60 * 1000;

// 주주 명부를 현재 기준 스냅샷으로 정리 — 동일 주주는 한 줄로, 실제 지분이 있는 주주만 노출.
// 최근 1년 내 0%로 전환된 주주(변동 테이블에서 shares_after=0 인 경우)는 하단 "0% 전환" 서브 섹션에 별도 표시한다.
function snapshotShareholders(
  rows: ShareholderRow[],
  changes: OwnershipChangeRow[],
): {
  holders: ShareholderRow[];
  recentZero: (ShareholderRow & { changedAt: string; position: string; delta: number | null })[];
} {
  const map = new Map<string, ShareholderRow>();
  for (const s of rows) {
    const existing = map.get(s.holder_name);
    // 동명이면 더 높은 지분율/최신 관계를 우선한다.
    if (!existing || (s.stake_pct ?? 0) > (existing.stake_pct ?? 0)) {
      map.set(s.holder_name, s);
    }
  }
  const all = Array.from(map.values()).sort((a, b) => (b.stake_pct ?? 0) - (a.stake_pct ?? 0));
  const holders = all.filter((s) => (s.stake_pct ?? 0) > 0);

  const now = Date.now();
  const changeByName = new Map<string, OwnershipChangeRow>();
  for (const c of changes) {
    if (!c.rcept_date || (c.shares_after ?? 0) !== 0) continue;
    const prev = changeByName.get(c.reporter);
    if (!prev || (c.rcept_date > (prev.rcept_date ?? ""))) {
      changeByName.set(c.reporter, c);
    }
  }

  const recentZero: (ShareholderRow & { changedAt: string; position: string; delta: number | null })[] = [];
  for (const s of all) {
    if ((s.stake_pct ?? 0) !== 0) continue;
    const ch = changeByName.get(s.holder_name);
    if (!ch || !ch.rcept_date) continue;
    const ts = Date.parse(ch.rcept_date);
    if (Number.isNaN(ts) || now - ts > ZERO_TRANSITION_WINDOW_MS) continue;
    recentZero.push({
      ...s,
      changedAt: ch.rcept_date,
      position: ch.position || "—",
      delta: ch.shares_delta,
    });
  }

  return { holders, recentZero };
}

// Set 토글 헬퍼 — 행/섹션 확장 상태.
function toggleInSet(prev: Set<string>, key: string): Set<string> {
  const next = new Set(prev);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

export default function OwnershipStructure({ code }: { code: string }) {
  const [state, setState] = useState<
    { status: "loading" | "ready" | "error"; data: OwnershipResponse | null; message?: string }
  >({ status: "loading", data: null });
  const [showAllSubs, setShowAllSubs] = useState(false);
  // 행 클릭 시 변동 내역 확장 — 주주·0%전환·5%+·기타 각 행.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // 0%전환·5%+·기타 섹션 자체 접힘(디폴트 collapsed).
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());
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
  const { holders: snapshotHolders, recentZero } = snapshotShareholders(shareholders, changes);

  // 주주 명부에 없는 최근 0% 전환 보고자도 "0% 전환"에 추가 표시.
  const zeroChangeRows = changes.filter(
    (c) => c.rcept_date && (c.shares_after ?? 0) === 0 && !snapshotHolders.some((s) => s.holder_name === c.reporter),
  );

  // elestock 변동을 보고자별로 묶어 주주·0%전환 행 클릭 시 확장.
  const changesByName = new Map<string, OwnershipChangeRow[]>();
  for (const c of changes) {
    if (!c.reporter) continue;
    const arr = changesByName.get(c.reporter) ?? [];
    arr.push(c);
    changesByName.set(c.reporter, arr);
  }
  const changesFor = (name: string): OwnershipChangeRow[] => changesByName.get(name) ?? [];

  // 5%+ 보고서를 보고자별로 묶어 행 클릭 시 개별 보고 상세 확장.
  const majorByName = new Map<string, MajorHolderRow[]>();
  for (const h of major_holders) {
    const arr = majorByName.get(h.repror) ?? [];
    arr.push(h);
    majorByName.set(h.repror, arr);
  }

  // 기타 최근 변동: 주주 명부·0%전환·5%+ 어디에도 매칭되지 않는 보고자(임원 등)의 변동.
  // 독립 "최근 지분 변동" 테이블을 폐지하고 per-shareholder 확장으로 옮겼으므로, 매칭되지 않은
  // 변동 데이터가 소실되지 않도록 별도 collapsed 섹션에 보존한다.
  const displayedNames = new Set<string>();
  snapshotHolders.forEach((s) => displayedNames.add(s.holder_name));
  recentZero.forEach((s) => displayedNames.add(s.holder_name));
  zeroChangeRows.forEach((c) => displayedNames.add(c.reporter));
  major_holders.forEach((h) => displayedNames.add(h.repror));
  const orphanReporters: { reporter: string; rows: OwnershipChangeRow[]; latest: OwnershipChangeRow }[] = [];
  for (const [reporter, rows] of changesByName) {
    if (displayedNames.has(reporter)) continue;
    const latest = rows.slice().sort((a, b) => (b.rcept_date ?? "").localeCompare(a.rcept_date ?? ""))[0];
    orphanReporters.push({ reporter, rows, latest });
  }
  orphanReporters.sort((a, b) => (b.latest.rcept_date ?? "").localeCompare(a.latest.rcept_date ?? ""));

  const groupedMajor = groupMajorHolders(major_holders);
  // 계약 체결(이전 미완료) 전액 주요체결 그룹을 실보유와 분리 — 혼동 방지.
  const majorActual = groupedMajor.filter((h) => !h.isContractOnly);
  const majorContract = groupedMajor.filter((h) => h.isContractOnly);
  const toggleRow = (key: string) => setExpanded((p) => toggleInSet(p, key));
  const toggleSection = (key: string) => setOpenSections((p) => toggleInSet(p, key));

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
          {/* 주주 명부 — 행 클릭 시 해당 주주의 elestock 소유변동 확장 */}
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
                {snapshotHolders.map((s) => {
                  const key = `sh-${s.holder_name}`;
                  const hist = changesFor(s.holder_name);
                  const expandable = hist.length > 0;
                  const isOpen = expanded.has(key);
                  return (
                    <Fragment key={key}>
                      <tr
                        className={expandable ? styles.clickable : undefined}
                        onClick={expandable ? () => toggleRow(key) : undefined}
                      >
                        <th className={styles.nameCol} scope="row">
                          {s.related_stock_code ? (
                            <Link
                              href={`/companies/${s.related_stock_code}`}
                              className={styles.link}
                              onClick={(e) => e.stopPropagation()}
                            >
                              {s.holder_name}
                            </Link>
                          ) : (
                            <span className={s.is_corporate ? styles.corp : styles.person}>
                              {s.holder_name}
                            </span>
                          )}
                          {expandable ? (
                            <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                          ) : null}
                        </th>
                        <td>{relateLabel(s.relate)}</td>
                        <td className={styles.num}>{fmtStake(s.stake_pct)}</td>
                      </tr>
                      {isOpen && expandable ? (
                        <tr className={styles.detailRow}>
                          <td colSpan={3}>
                            <ChangeHistoryDetail rows={hist} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}

          {/* 최근 1년 내 0%로 전환된 주주 — 섹션 디폴트 collapsed, 행 클릭 시 변동 확장 */}
          {(recentZero.length > 0 || zeroChangeRows.length > 0) && (
            <CollapsibleSection
              title="최근 1년 내 0% 전환"
              count={recentZero.length + zeroChangeRows.length}
              sectionKey="zero"
              open={openSections.has("zero")}
              onToggle={toggleSection}
            >
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.nameCol} scope="col">주주</th>
                      <th scope="col">직위/관계</th>
                      <th scope="col">전환일</th>
                      <th scope="col">변동주식수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentZero.map((s) => {
                      const key = `zero-${s.holder_name}`;
                      const hist = changesFor(s.holder_name);
                      const expandable = hist.length > 0;
                      const isOpen = expanded.has(key);
                      return (
                        <Fragment key={key}>
                          <tr
                            className={expandable ? styles.clickable : undefined}
                            onClick={expandable ? () => toggleRow(key) : undefined}
                          >
                            <th className={styles.nameCol} scope="row">
                              {s.related_stock_code ? (
                                <Link
                                  href={`/companies/${s.related_stock_code}`}
                                  className={styles.link}
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  {s.holder_name}
                                </Link>
                              ) : (
                                <span className={s.is_corporate ? styles.corp : styles.person}>
                                  {s.holder_name}
                                </span>
                              )}
                              {expandable ? (
                                <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                              ) : null}
                            </th>
                            <td>{s.position}</td>
                            <td>{s.changedAt}</td>
                            <td className={styles.num}>
                              {s.delta !== null
                                ? `${s.delta > 0 ? "+" : ""}${s.delta.toLocaleString("ko-KR")}주`
                                : "—"}
                            </td>
                          </tr>
                          {isOpen && expandable ? (
                            <tr className={styles.detailRow}>
                              <td colSpan={4}>
                                <ChangeHistoryDetail rows={hist} />
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                    {zeroChangeRows.map((c) => {
                      const key = `zero-${c.reporter}-${c.rcept_no}`;
                      const hist = changesFor(c.reporter);
                      const expandable = hist.length > 0;
                      const isOpen = expanded.has(key);
                      return (
                        <Fragment key={key}>
                          <tr
                            className={expandable ? styles.clickable : undefined}
                            onClick={expandable ? () => toggleRow(key) : undefined}
                          >
                            <th className={styles.nameCol} scope="row">
                              <span className={styles.person}>{c.reporter}</span>
                              {expandable ? (
                                <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                              ) : null}
                            </th>
                            <td>{c.position || "—"}</td>
                            <td>{c.rcept_date ?? "—"}</td>
                            <td className={styles.num}>
                              {c.shares_delta !== null
                                ? `${c.shares_delta > 0 ? "+" : ""}${c.shares_delta.toLocaleString("ko-KR")}주`
                                : "—"}
                            </td>
                          </tr>
                          {isOpen && expandable ? (
                            <tr className={styles.detailRow}>
                              <td colSpan={4}>
                                <ChangeHistoryDetail rows={hist} />
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CollapsibleSection>
          )}

          {/* 5%+ 대량보유주주 — 섹션 디폴트 collapsed, 행 클릭 시 개별 보고 상세 확장.
              계약 체결(이전 미완료) 전액 주요체결 주주는 실보유와 섞이면 혼동되므로 별도 그룹으로 분리. */}
          {major_holders.length > 0 && (
            <CollapsibleSection
              title="5%+ 대량보유주주"
              count={groupedMajor.length}
              sectionKey="major"
              open={openSections.has("major")}
              onToggle={toggleSection}
            >
              {majorActual.length > 0 ? (
                <div className={styles.scroll}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th className={styles.nameCol} scope="col">보고자</th>
                        <th scope="col">보유비율</th>
                        <th scope="col">보유주식</th>
                        <th scope="col">변경 이력</th>
                      </tr>
                    </thead>
                    <tbody>
                      {majorActual.map((h) => {
                        const key = `mh-${h.repror}`;
                        const detail = majorByName.get(h.repror) ?? [];
                        const isOpen = expanded.has(key);
                        return (
                          <Fragment key={key}>
                            <tr className={styles.clickable} onClick={() => toggleRow(key)}>
                              <th className={styles.nameCol} scope="row">
                                {h.repror}
                                <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                              </th>
                              <td className={styles.num}>{fmtStake(h.stkrt)}</td>
                              <td className={styles.num}>{fmtShares(h.stkqy)}</td>
                              <td className={styles.historyCell}>{h.history}</td>
                            </tr>
                            {isOpen ? (
                              <tr className={styles.detailRow}>
                                <td colSpan={4}>
                                  <MajorHolderDetail rows={detail} />
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {majorContract.length > 0 ? (
                <div className={styles.contractGroup}>
                  <div className={styles.contractLabel}>
                    ⚠ 계약 체결 (이전 미완료)
                  </div>
                  <div className={styles.scroll}>
                    <table className={styles.table}>
                      <thead>
                        <tr>
                          <th className={styles.nameCol} scope="col">보고자</th>
                          <th scope="col">계약 비율</th>
                          <th scope="col">계약 주식</th>
                          <th scope="col">변경 이력</th>
                        </tr>
                      </thead>
                      <tbody>
                        {majorContract.map((h) => {
                          const key = `mh-${h.repror}`;
                          const detail = majorByName.get(h.repror) ?? [];
                          const isOpen = expanded.has(key);
                          return (
                            <Fragment key={key}>
                              <tr className={styles.clickable} onClick={() => toggleRow(key)}>
                                <th className={styles.nameCol} scope="row">
                                  {h.repror}
                                  <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                                </th>
                                <td className={styles.num}>{fmtStake(h.stkrt)}</td>
                                <td className={styles.num}>{fmtShares(h.stkqy)}</td>
                                <td className={styles.historyCell}>{h.history}</td>
                              </tr>
                              {isOpen ? (
                                <tr className={styles.detailRow}>
                                  <td colSpan={4}>
                                    <MajorHolderDetail rows={detail} />
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className={styles.contractNote}>
                    주식매매계약 체결분으로 명의개서(이전)가 완료되지 않아 실제 지분이 아닙니다.
                  </div>
                </div>
              ) : null}
            </CollapsibleSection>
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

          {/* 기타 최근 변동 — 어디에도 매칭 안 되는 임원·주요주주 소유변동 보존(collapsed) */}
          {orphanReporters.length > 0 && (
            <CollapsibleSection
              title="기타 최근 변동"
              count={orphanReporters.length}
              sectionKey="etc"
              open={openSections.has("etc")}
              onToggle={toggleSection}
              stale={changes_stale}
            >
              <p className={styles.subNote}>주주 명부·5%+에 속하지 않는 보고자의 소유변동</p>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.nameCol} scope="col">보고자</th>
                      <th scope="col">직위</th>
                      <th scope="col">최근 변동</th>
                      <th scope="col">건수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orphanReporters.map(({ reporter, rows, latest }) => {
                      const key = `etc-${reporter}`;
                      const isOpen = expanded.has(key);
                      return (
                        <Fragment key={key}>
                          <tr className={styles.clickable} onClick={() => toggleRow(key)}>
                            <th className={styles.nameCol} scope="row">
                              {reporter}
                              <span className={styles.rowArrow}>{isOpen ? "▾" : "▸"}</span>
                            </th>
                            <td>{latest.position || "—"}</td>
                            <td>{latest.rcept_date ?? "—"}</td>
                            <td className={styles.num}>{rows.length}건</td>
                          </tr>
                          {isOpen ? (
                            <tr className={styles.detailRow}>
                              <td colSpan={4}>
                                <ChangeHistoryDetail rows={rows} />
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CollapsibleSection>
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

// 접힘 섹션 — 헤더 클릭으로 토글. 0%전환·5%+·기타는 디폴트 collapsed.
function CollapsibleSection({
  title,
  count,
  sectionKey,
  open,
  onToggle,
  stale,
  children,
}: {
  title: string;
  count?: number;
  sectionKey: string;
  open: boolean;
  onToggle: (key: string) => void;
  stale?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.subSection}>
      <button
        type="button"
        className={styles.sectionToggle}
        aria-expanded={open}
        onClick={() => onToggle(sectionKey)}
      >
        <span className={styles.sectionArrow}>{open ? "▾" : "▸"}</span>
        <span className={styles.subSectionTitle}>{title}</span>
        {count !== undefined ? <span className={styles.countBadge}>{count}</span> : null}
        {stale ? <span className={styles.staleTag}>갱신 중</span> : null}
      </button>
      {open ? children : null}
    </div>
  );
}

// elestock 소유변동 확장 — 주주·0%전환·기타 행 클릭 시.
function ChangeHistoryDetail({ rows }: { rows: OwnershipChangeRow[] }) {
  const sorted = rows.slice().sort((a, b) => (b.rcept_date ?? "").localeCompare(a.rcept_date ?? ""));
  return (
    <ul className={styles.changeList}>
      {sorted.map((c) => {
        const delta = c.shares_delta;
        const positive = delta !== null && delta >= 0;
        return (
          <li key={c.rcept_no} className={styles.changeItem}>
            <span className={styles.changeDate}>{c.rcept_date ?? "—"}</span>
            <span className={styles.changePos}>{c.position || "—"}</span>
            <span className={`${styles.changeDelta} ${positive ? styles.up : styles.down}`}>
              {delta === null ? "—" : `${positive ? "+" : ""}${delta.toLocaleString("ko-KR")}주`}
            </span>
            <span className={styles.changeAfter}>변동후 {fmtShares(c.shares_after)}</span>
            {c.reason ? <span className={styles.changeReason}>{c.reason}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

// 5%+ 보고자의 개별 대량보유 상황보고 확장 — 행 클릭 시.
function MajorHolderDetail({ rows }: { rows: MajorHolderRow[] }) {
  const sorted = rows.slice().sort((a, b) => (b.rcept_dt ?? "").localeCompare(a.rcept_dt ?? ""));
  return (
    <ul className={styles.changeList}>
      {sorted.map((h, i) => (
        <li key={`${h.rcept_dt}-${i}`} className={styles.changeItem}>
          <span className={styles.changeDate}>{fmtDate(h.rcept_dt)}</span>
          <span className={styles.changeAfter}>보유 {fmtStake(h.stkrt)}</span>
          <span className={styles.changeAfter}>{fmtShares(h.stkqy)}주</span>
          {(h.ctr_stkqy ?? 0) > 0 ? (
            <span className={styles.changeReason}>
              계약 체결 {fmtStake(h.ctr_stkrt)} · {fmtShares(h.ctr_stkqy)}주 (이전 미완료)
            </span>
          ) : null}
          {h.report_resn ? <span className={styles.changeReason}>{h.report_resn}</span> : null}
        </li>
      ))}
    </ul>
  );
}