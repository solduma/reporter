"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { deleteHolding, fetchHoldings, saveHolding } from "@/lib/api";
import type { Holding } from "@/lib/types";

import styles from "./page.module.css";

// 6자리 종목코드 + 수량·평단(양수) 검증. 손절선·메모는 선택.
function isValidCode(code: string): boolean {
  return /^\d{6}$/.test(code.trim());
}

export default function PortfolioPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
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
      setHoldings(await fetchHoldings());
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
        <table className={styles.table}>
          <thead>
            <tr>
              <th>종목</th>
              <th className={styles.num}>수량</th>
              <th className={styles.num}>평단</th>
              <th className={styles.num}>손절선</th>
              <th>메모</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => (
              <tr key={h.stock_code}>
                <td>
                  <Link href={`/companies/${h.stock_code}`} className={styles.stockLink}>
                    {h.stock_name ?? h.stock_code}
                  </Link>
                  <span className={styles.code}>{h.stock_code}</span>
                </td>
                <td className={styles.num}>{h.shares.toLocaleString()}</td>
                <td className={styles.num}>{h.avg_cost.toLocaleString()}</td>
                <td className={styles.num}>{h.stop_loss ? h.stop_loss.toLocaleString() : "—"}</td>
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
      )}
    </div>
  );
}
