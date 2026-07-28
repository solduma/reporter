"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchBusinessPending,
  promoteBusinessPending,
  rejectBusinessPending,
} from "@/lib/api";
import type { BusinessLayer, BusinessPendingNode } from "@/lib/types";

import styles from "./page.module.css";

const LAYER_TABS: { key: "all" | BusinessLayer; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "company", label: "기업" },
  { key: "industry", label: "산업" },
  { key: "product", label: "제품" },
  { key: "raw_material", label: "원재료" },
  { key: "segment", label: "부문" },
];

const LAYER_LABEL: Record<BusinessLayer, string> = {
  company: "기업",
  industry: "산업",
  product: "제품",
  raw_material: "원재료",
  segment: "부문",
};

// 신규 canonical_id 접두어 — 백엔드 _CANON_PREFIX 와 일치.
const NEW_PREFIX: Record<BusinessLayer, string> = {
  company: "CMP_KRX_",
  industry: "IND_GICS_",
  product: "PRD_",
  raw_material: "MAT_",
  segment: "SEG_",
};

const PAGE_SIZE = 50;

export default function PendingReviewPage() {
  const [items, setItems] = useState<BusinessPendingNode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layer, setLayer] = useState<"all" | BusinessLayer>("all");
  const [stockFilter, setStockFilter] = useState("");
  const [offset, setOffset] = useState(0);
  // 노드별 신규 ID 입력값.
  const [newCid, setNewCid] = useState<Record<number, string>>({});
  // 노드별 처리 중·오류.
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [nodeErr, setNodeErr] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchBusinessPending({
        node_type: layer === "all" ? undefined : layer,
        stock_code: stockFilter.trim() || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setItems(data.pending);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "목록 로드 실패");
    } finally {
      setLoading(false);
    }
  }, [layer, stockFilter, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // 필터 변경 시 첫 페이지로.
  const onLayerChange = (k: "all" | BusinessLayer) => {
    setLayer(k);
    setOffset(0);
  };
  const onStockSearch = () => {
    setOffset(0);
    void load();
  };

  const afterAction = useCallback(
    (removedId: number) => {
      setItems((prev) => {
        const next = prev.filter((n) => n.id !== removedId);
        // 페이지가 비고 이전 페이지가 있으면 한 페이지 뒤로.
        if (next.length === 0 && offset > 0) {
          setOffset(Math.max(0, offset - PAGE_SIZE));
        }
        return next;
      });
      setTotal((t) => Math.max(0, t - 1));
    },
    [offset],
  );

  const onMerge = useCallback(
    async (node: BusinessPendingNode, canonicalId: string) => {
      setBusy((b) => ({ ...b, [node.id]: true }));
      setNodeErr((e) => ({ ...e, [node.id]: "" }));
      try {
        await promoteBusinessPending(node.id, canonicalId, "merge");
        afterAction(node.id);
      } catch (err) {
        setNodeErr((e) => ({
          ...e,
          [node.id]: err instanceof Error ? err.message : "승격 실패",
        }));
      } finally {
        setBusy((b) => ({ ...b, [node.id]: false }));
      }
    },
    [afterAction],
  );

  const onNew = useCallback(
    async (node: BusinessPendingNode) => {
      const cid = (newCid[node.id] ?? "").trim();
      if (!cid) {
        setNodeErr((e) => ({ ...e, [node.id]: "canonical_id 를 입력하세요" }));
        return;
      }
      setBusy((b) => ({ ...b, [node.id]: true }));
      setNodeErr((e) => ({ ...e, [node.id]: "" }));
      try {
        await promoteBusinessPending(node.id, cid, "new");
        afterAction(node.id);
      } catch (err) {
        setNodeErr((e) => ({
          ...e,
          [node.id]: err instanceof Error ? err.message : "승격 실패",
        }));
      } finally {
        setBusy((b) => ({ ...b, [node.id]: false }));
      }
    },
    [newCid, afterAction],
  );

  const onReject = useCallback(
    async (node: BusinessPendingNode) => {
      setBusy((b) => ({ ...b, [node.id]: true }));
      setNodeErr((e) => ({ ...e, [node.id]: "" }));
      try {
        await rejectBusinessPending(node.id);
        afterAction(node.id);
      } catch (err) {
        setNodeErr((e) => ({
          ...e,
          [node.id]: err instanceof Error ? err.message : "거부 실패",
        }));
      } finally {
        setBusy((b) => ({ ...b, [node.id]: false }));
      }
    },
    [afterAction],
  );

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>온톨로지 검수</h1>
        <p className={styles.subtitle}>
          정규화 실패(pending_review) 노드를 사람이 canonical 로 승격. merge=기존 정준에 합류, new=신규
          정준 발급, reject=숨김. 자동병합 금지 원칙.
        </p>
        <div className={styles.toolbar}>
          <div className={styles.tabs}>
            {LAYER_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`${styles.tab} ${layer === t.key ? styles.tabActive : ""}`}
                onClick={() => onLayerChange(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className={styles.stockBox}>
            <input
              className={styles.stockInput}
              type="text"
              placeholder="종목코드 (예: 005930)"
              value={stockFilter}
              onChange={(e) => setStockFilter(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onStockSearch()}
            />
            <button type="button" className={styles.stockBtn} onClick={onStockSearch}>
              조회
            </button>
          </div>
          <span className={styles.count}>총 {total}건</span>
        </div>
      </header>

      {error ? <div className={styles.error}>{error}</div> : null}
      {loading ? <div className={styles.loading}>불러오는 중…</div> : null}

      <div className={styles.list}>
        {items.length === 0 && !loading ? (
          <div className={styles.empty}>검수할 pending 노드가 없습니다.</div>
        ) : null}
        {items.map((node) => {
          const prefix = NEW_PREFIX[node.node_type];
          const err = nodeErr[node.id];
          const isBusy = busy[node.id];
          return (
            <div key={node.id} className={styles.row}>
              <div className={styles.rowHead}>
                <span className={`${styles.badge} ${styles[`badge_${node.node_type}`] ?? ""}`}>
                  {LAYER_LABEL[node.node_type]}
                </span>
                <span className={styles.name}>{node.korean_name}</span>
                {node.english_name ? (
                  <span className={styles.english}>{node.english_name}</span>
                ) : null}
                <span className={styles.stock}>{node.stock_code}</span>
              </div>

              <div className={styles.rowBody}>
                <div className={styles.cands}>
                  <div className={styles.candsLabel}>승격 후보(merge)</div>
                  {node.candidates.length === 0 ? (
                    <span className={styles.noCand}>후보 없음 — new 로 직접 발급</span>
                  ) : (
                    node.candidates.map((c) => (
                      <div key={c.canonical_id} className={styles.cand}>
                        <span className={styles.candName}>{c.korean_name}</span>
                        <span className={styles.candId}>{c.canonical_id}</span>
                        <span className={styles.score}>{Math.round(c.score * 100)}%</span>
                        <button
                          type="button"
                          className={styles.mergeBtn}
                          disabled={isBusy}
                          onClick={() => onMerge(node, c.canonical_id)}
                        >
                          merge
                        </button>
                      </div>
                    ))
                  )}
                </div>

                <div className={styles.actions}>
                  <div className={styles.newBox}>
                    <input
                      className={styles.newInput}
                      type="text"
                      placeholder={`${prefix}…`}
                      value={newCid[node.id] ?? ""}
                      onChange={(e) =>
                        setNewCid((m) => ({ ...m, [node.id]: e.target.value }))
                      }
                      onKeyDown={(e) => e.key === "Enter" && onNew(node)}
                    />
                    <button
                      type="button"
                      className={styles.newBtn}
                      disabled={isBusy}
                      onClick={() => onNew(node)}
                    >
                      new
                    </button>
                  </div>
                  <button
                    type="button"
                    className={styles.rejectBtn}
                    disabled={isBusy}
                    onClick={() => onReject(node)}
                  >
                    reject
                  </button>
                </div>
              </div>

              {err ? <div className={styles.nodeErr}>{err}</div> : null}
            </div>
          );
        })}
      </div>

      {totalPages > 1 ? (
        <div className={styles.pager}>
          <button
            type="button"
            className={styles.pageBtn}
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            이전
          </button>
          <span className={styles.pageInfo}>
            {page} / {totalPages}
          </span>
          <button
            type="button"
            className={styles.pageBtn}
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            다음
          </button>
        </div>
      ) : null}
    </div>
  );
}