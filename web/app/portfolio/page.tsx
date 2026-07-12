"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { deleteHolding, fetchPortfolio, saveHolding } from "@/lib/api";
import type { Holding, PortfolioSummary, SectorWeight, StopStatus } from "@/lib/types";

import styles from "./page.module.css";

// 6자리 종목코드 + 수량·평단(양수) 검증. 손절선·메모는 선택.
function isValidCode(code: string): boolean {
  return /^\d{6}$/.test(code.trim());
}

const WON = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : Math.round(n).toLocaleString();
const PCT = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;

// 손익 부호 색: 이익 빨강 / 손실 파랑(한국 관례).
function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined || n === 0) return "";
  return n > 0 ? styles.up : styles.down;
}

const STOP_LABEL: Record<StopStatus, string> = {
  none: "",
  ok: "",
  near: "손절 근접",
  hit: "손절 도달",
};

export default function PortfolioPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [sectors, setSectors] = useState<SectorWeight[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 추가 폼 상태
  const [code, setCode] = useState("");
  const [shares, setShares] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const view = await fetchPortfolio();
      setHoldings(view.holdings);
      setSummary(view.summary);
      setSectors(view.sectors);
    } catch (e) {
      setError(e instanceof Error ? e.message : "보유종목을 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const s = Number(shares);
    const c = Number(avgCost);
    if (!isValidCode(code) || !(s > 0) || !(c > 0)) {
      setError("종목코드(6자리)·수량·평단을 올바르게 입력하세요");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveHolding(code.trim(), {
        shares: s,
        avg_cost: c,
        stop_loss: stopLoss ? Number(stopLoss) : null,
        note: note.trim() || null,
      });
      setCode("");
      setShares("");
      setAvgCost("");
      setStopLoss("");
      setNote("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (c: string) => {
    try {
      await deleteHolding(c);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>내 보유종목</h1>
      <p className={styles.sub}>
        수량·평단·손절선을 기록합니다. 관심종목(자동 저장)과 별개로, 실제 보유분을 관리합니다.
      </p>

      <form className={styles.form} onSubmit={onAdd}>
        <input
          className={styles.input}
          placeholder="종목코드(6자리)"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
        />
        <input
          className={styles.input}
          placeholder="수량"
          value={shares}
          onChange={(e) => setShares(e.target.value)}
          inputMode="decimal"
        />
        <input
          className={styles.input}
          placeholder="평단(원)"
          value={avgCost}
          onChange={(e) => setAvgCost(e.target.value)}
          inputMode="decimal"
        />
        <input
          className={styles.input}
          placeholder="손절선(원, 선택)"
          value={stopLoss}
          onChange={(e) => setStopLoss(e.target.value)}
          inputMode="decimal"
        />
        <input
          className={styles.inputWide}
          placeholder="메모(선택)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button className={styles.addBtn} type="submit" disabled={saving}>
          {saving ? "저장 중…" : "추가/수정"}
        </button>
      </form>

      {error ? <p className={styles.error}>{error}</p> : null}

      {loading ? (
        <p className={styles.status}>불러오는 중…</p>
      ) : holdings.length === 0 ? (
        <p className={styles.status}>보유종목이 없습니다. 위에서 추가하세요.</p>
      ) : (
        <>
          {summary ? (
            <div className={styles.summary}>
              <div className={styles.summaryItem}>
                <span className={styles.summaryLabel}>평가액</span>
                <span className={styles.summaryValue}>{WON(summary.total_value)}</span>
              </div>
              <div className={styles.summaryItem}>
                <span className={styles.summaryLabel}>원가</span>
                <span className={styles.summaryValue}>{WON(summary.total_cost)}</span>
              </div>
              <div className={styles.summaryItem}>
                <span className={styles.summaryLabel}>평가손익</span>
                <span className={`${styles.summaryValue} ${pnlClass(summary.total_pnl)}`}>
                  {WON(summary.total_pnl)} ({PCT(summary.total_pnl_pct)})
                </span>
              </div>
              {summary.stop_hit > 0 || summary.stop_near > 0 ? (
                <div className={styles.summaryItem}>
                  <span className={styles.summaryLabel}>손절 경보</span>
                  <span className={styles.summaryValue}>
                    {summary.stop_hit > 0 ? (
                      <span className={styles.stopHit}>도달 {summary.stop_hit}</span>
                    ) : null}
                    {summary.stop_near > 0 ? (
                      <span className={styles.stopNear}> 근접 {summary.stop_near}</span>
                    ) : null}
                  </span>
                </div>
              ) : null}
            </div>
          ) : null}

          {sectors.length > 0 ? (
            <div className={styles.sectors}>
              <span className={styles.sectorsLabel}>섹터 분산</span>
              <div className={styles.sectorBar}>
                {sectors.map((s) => (
                  <span
                    key={s.sector}
                    className={styles.sectorSeg}
                    style={{ width: `${s.weight_pct}%` }}
                    title={`${s.sector} ${s.weight_pct}%`}
                  >
                    {s.weight_pct >= 12 ? `${s.sector} ${s.weight_pct}%` : ""}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <table className={styles.table}>
            <thead>
              <tr>
                <th>종목</th>
                <th className={styles.num}>수량</th>
                <th className={styles.num}>평단</th>
                <th className={styles.num}>현재가</th>
                <th className={styles.num}>평가손익</th>
                <th className={styles.num}>손절선</th>
                <th>메모</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => (
                <tr key={h.stock_code} className={h.stop_status === "hit" ? styles.rowHit : undefined}>
                  <td>
                    <Link href={`/companies/${h.stock_code}`} className={styles.stockLink}>
                      {h.stock_name ?? h.stock_code}
                    </Link>
                    <span className={styles.code}>{h.stock_code}</span>
                  </td>
                  <td className={styles.num}>{h.shares.toLocaleString()}</td>
                  <td className={styles.num}>{WON(h.avg_cost)}</td>
                  <td className={styles.num}>{WON(h.current_price)}</td>
                  <td className={`${styles.num} ${pnlClass(h.pnl)}`}>
                    {h.pnl === null ? "—" : `${WON(h.pnl)} (${PCT(h.pnl_pct)})`}
                  </td>
                  <td className={styles.num}>
                    {h.stop_loss ? WON(h.stop_loss) : "—"}
                    {STOP_LABEL[h.stop_status] ? (
                      <span className={h.stop_status === "hit" ? styles.stopHit : styles.stopNear}>
                        {" "}
                        {STOP_LABEL[h.stop_status]}
                      </span>
                    ) : null}
                  </td>
                  <td className={styles.noteCell}>{h.note ?? ""}</td>
                  <td>
                    <button className={styles.delBtn} type="button" onClick={() => void onDelete(h.stock_code)}>
                      삭제
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
