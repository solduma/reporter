"use client";

import { useEffect, useState } from "react";

import { deleteHolding, fetchHoldings, saveHolding } from "@/lib/api";
import type { Holding } from "@/lib/types";

import styles from "./HoldingBadge.module.css";

// 종목 헤더에 보유 상태를 보이고, 인라인으로 추가/수정/삭제한다. 보유가 아니면 "보유 추가" 버튼.
export default function HoldingBadge({ code }: { code: string }) {
  const [holding, setHolding] = useState<Holding | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [shares, setShares] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const all = await fetchHoldings();
      const h = all.find((x) => x.stock_code === code) ?? null;
      setHolding(h);
    } catch {
      setHolding(null);
    } finally {
      setLoaded(true);
    }
  };

  useEffect(() => {
    setLoaded(false);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  const openEdit = () => {
    setShares(holding ? String(holding.shares) : "");
    setAvgCost(holding ? String(holding.avg_cost) : "");
    setStopLoss(holding?.stop_loss ? String(holding.stop_loss) : "");
    setEditing(true);
  };

  const onSave = async () => {
    const s = Number(shares);
    const c = Number(avgCost);
    if (!(s > 0) || !(c > 0)) {
      return;
    }
    setSaving(true);
    try {
      await saveHolding(code, {
        shares: s,
        avg_cost: c,
        stop_loss: stopLoss ? Number(stopLoss) : null,
      });
      setEditing(false);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    setSaving(true);
    try {
      await deleteHolding(code);
      setEditing(false);
      await load();
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) {
    return null;
  }

  if (editing) {
    return (
      <span className={styles.editor}>
        <input
          className={styles.input}
          placeholder="수량"
          value={shares}
          onChange={(e) => setShares(e.target.value)}
          inputMode="decimal"
        />
        <input
          className={styles.input}
          placeholder="평단"
          value={avgCost}
          onChange={(e) => setAvgCost(e.target.value)}
          inputMode="decimal"
        />
        <input
          className={styles.input}
          placeholder="손절선"
          value={stopLoss}
          onChange={(e) => setStopLoss(e.target.value)}
          inputMode="decimal"
        />
        <button className={styles.save} type="button" onClick={() => void onSave()} disabled={saving}>
          저장
        </button>
        {holding ? (
          <button className={styles.del} type="button" onClick={() => void onDelete()} disabled={saving}>
            삭제
          </button>
        ) : null}
        <button className={styles.cancel} type="button" onClick={() => setEditing(false)}>
          취소
        </button>
      </span>
    );
  }

  if (!holding) {
    return (
      <button className={styles.add} type="button" onClick={openEdit}>
        + 보유 추가
      </button>
    );
  }

  const pnlClass =
    holding.pnl === null || holding.pnl === 0 ? "" : holding.pnl > 0 ? styles.up : styles.down;
  return (
    <button
      className={holding.stop_status === "hit" ? `${styles.chip} ${styles.chipHit}` : styles.chip}
      type="button"
      onClick={openEdit}
      title="보유 정보 수정"
    >
      <span className={styles.chipLabel}>보유</span>
      <span>{holding.shares.toLocaleString()}주</span>
      {holding.pnl_pct !== null ? (
        <span className={pnlClass}>
          {holding.pnl_pct > 0 ? "+" : ""}
          {holding.pnl_pct.toFixed(1)}%
        </span>
      ) : null}
      {holding.stop_status === "hit" ? <span className={styles.hit}>손절도달</span> : null}
      {holding.stop_status === "near" ? <span className={styles.near}>손절근접</span> : null}
    </button>
  );
}
