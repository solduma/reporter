"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import StockSearch from "@/components/StockSearch";
import { fetchBusinessExplore } from "@/lib/api";
import type {
  BusinessExploreEdge,
  BusinessExploreNode,
  BusinessLayer,
} from "@/lib/types";

import styles from "./page.module.css";

const OntologyGraph = dynamic(() => import("./OntologyGraph"), {
  ssr: false,
  loading: () => <div className={styles.canvasLoading}>그래프 로드 중…</div>,
});

const LAYER_TABS: { key: "all" | BusinessLayer; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "company", label: "기업" },
  { key: "industry", label: "산업" },
  { key: "product", label: "제품" },
  { key: "raw_material", label: "원재료" },
];

const EDGE_LABEL: Record<string, string> = {
  manufactures: "생산",
  uses_material: "원재료 사용",
  operates_in: "운영 산업",
  competes_with: "경쟁",
  supplies: "공급받음",
  supplies_to: "납품",
  has_segment: "부문",
  part_of_value_chain: "가치사슬",
  sibling_industry: "형제 산업",
  is_also_material: "겸 원재료",
};

export default function OntologyExplorePage() {
  const [nodesMap, setNodesMap] = useState<Record<string, BusinessExploreNode>>({});
  const [edgesMap, setEdgesMap] = useState<Record<string, BusinessExploreEdge>>({});
  const [focalId, setFocalId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [layer, setLayer] = useState<"all" | BusinessLayer>("all");
  const [breadcrumb, setBreadcrumb] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadExplore = useCallback(
    async (nodeId: string, reset: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchBusinessExplore(nodeId);
        setNodesMap((prev) => {
          const next: Record<string, BusinessExploreNode> = reset ? {} : { ...prev };
          next[data.focal.id] = data.focal;
          for (const n of data.neighbors) next[n.id] = n;
          return next;
        });
        setEdgesMap((prev) => {
          const next: Record<string, BusinessExploreEdge> = reset ? {} : { ...prev };
          for (const e of data.edges) {
            next[`${e.src}|${e.dst}|${e.edge_type}`] = e;
          }
          return next;
        });
        setFocalId(data.focal.id);
        setSelectedId(data.focal.id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "탐색 실패");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const onPickStock = useCallback(
    (code: string, _name: string) => {
      setBreadcrumb([]);
      void loadExplore(`CMP_KRX_${code}`, true);
    },
    [loadExplore],
  );

  const onNodeExpand = useCallback(
    (id: string) => {
      setBreadcrumb((prev) => (prev[prev.length - 1] === id ? prev : [...prev, id]));
      void loadExplore(id, false);
    },
    [loadExplore],
  );

  const onNodeSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const onBreadcrumb = useCallback(
    (id: string) => {
      void loadExplore(id, false);
    },
    [loadExplore],
  );

  // 깊은 링크(?code=005930) 자동 로드 — 공유·스크린샷 검수용.
  useEffect(() => {
    if (focalId) return;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    if (code && /^\d{6}$/.test(code)) {
      void loadExplore(`CMP_KRX_${code}`, true);
    }
  }, [focalId, loadExplore]);

  // ── 가시 노드/엣지(레이어 필터) ──────────────────────────────────────────
  const visibleNodes = useMemo(() => {
    const all = Object.values(nodesMap);
    if (layer === "all") return all;
    return all.filter((n) => n.id === focalId || n.node_type === layer);
  }, [nodesMap, layer, focalId]);

  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  const visibleEdges = useMemo(
    () =>
      Object.values(edgesMap).filter(
        (e) => visibleIds.has(e.src) && visibleIds.has(e.dst),
      ),
    [edgesMap, visibleIds],
  );

  // ── 상세 패널: 선택 노드 + 인접 엣지 ─────────────────────────────────────
  const selectedNode = selectedId ? nodesMap[selectedId] ?? null : null;
  const incidentEdges = useMemo(() => {
    if (!selectedId) return [];
    return Object.values(edgesMap).filter(
      (e) => e.src === selectedId || e.dst === selectedId,
    );
  }, [edgesMap, selectedId]);

  const neighborGroups = useMemo(() => {
    const groups: Record<string, { neighbor: BusinessExploreNode; edge: BusinessExploreEdge }[]> = {};
    for (const e of incidentEdges) {
      const otherId = e.src === selectedId ? e.dst : e.src;
      const neighbor = nodesMap[otherId];
      if (!neighbor) continue;
      const key = e.edge_type;
      (groups[key] ??= []).push({ neighbor, edge: e });
    }
    return groups;
  }, [incidentEdges, nodesMap, selectedId]);

  return (
    <div className={styles.page}>
      <header className={styles.toolbar}>
        <div className={styles.searchWrap}>
          <StockSearch onPick={onPickStock} placeholder="탐색할 기업 검색 (예: 삼성전자)" />
        </div>
        <div className={styles.tabs}>
          {LAYER_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`${styles.tab} ${layer === t.key ? styles.tabActive : ""}`}
              onClick={() => setLayer(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      {breadcrumb.length > 1 ? (
        <div className={styles.breadcrumb}>
          {breadcrumb.map((id) => {
            const n = nodesMap[id];
            return (
              <button
                key={id}
                type="button"
                className={`${styles.crumb} ${id === focalId ? styles.crumbActive : ""}`}
                onClick={() => onBreadcrumb(id)}
              >
                {n?.korean_name ?? id}
              </button>
            );
          })}
        </div>
      ) : null}

      <div className={styles.main}>
        <div className={styles.graphArea}>
          {focalId ? (
            <OntologyGraph
              nodes={visibleNodes}
              edges={visibleEdges.map((e) => ({
                src: e.src,
                dst: e.dst,
                edge_type: e.edge_type,
                share: e.share,
              }))}
              focalId={focalId}
              selectedId={selectedId}
              onNodeExpand={onNodeExpand}
              onNodeSelect={onNodeSelect}
            />
          ) : (
            <div className={styles.empty}>
              {error ? <span className={styles.error}>{error}</span> : null}
              <p>기업을 검색하면 비즈니스 온톨로지 그래프가 펼쳐집니다.</p>
              <p className={styles.hint}>
                기업 → 산업·제품·원재료·동종업 을 4계층 필터로 전환하며 탐색. 노드 클릭으로 상세,
                ⟳ 버튼으로 이웃 확장.
              </p>
            </div>
          )}
          {loading ? <div className={styles.loadingOverlay}>탐색 중…</div> : null}
        </div>

        <aside className={styles.panel}>
          {selectedNode ? (
            <>
              <div className={`${styles.nodeHead} ${styles[`layer_${selectedNode.node_type}`] ?? ""}`}>
                <span className={`${styles.badge} ${styles[`badge_${selectedNode.node_type}`] ?? ""}`}>
                  {{ company: "기업", industry: "산업", product: "제품", raw_material: "원재료", segment: "부문" }[selectedNode.node_type]}
                </span>
                <span className={styles.headName}>{selectedNode.korean_name}</span>
              </div>
              <dl className={styles.meta}>
                <div><dt>정준 ID</dt><dd className={styles.mono}>{selectedNode.id}</dd></div>
                {selectedNode.english_name ? (
                  <div><dt>영문</dt><dd>{selectedNode.english_name}</dd></div>
                ) : null}
                {selectedNode.aliases?.length ? (
                  <div><dt>별칭</dt><dd>{selectedNode.aliases.join(", ")}</dd></div>
                ) : null}
                {selectedNode.gics_code ? (
                  <div><dt>GICS</dt><dd className={styles.mono}>{selectedNode.gics_code}</dd></div>
                ) : null}
                {selectedNode.commodity_type ? (
                  <div><dt>상품분류</dt><dd>{selectedNode.commodity_type}</dd></div>
                ) : null}
                {selectedNode.stock_code ? (
                  <div><dt>종목코드</dt><dd className={styles.mono}>{selectedNode.stock_code}</dd></div>
                ) : null}
                {selectedNode.confidence !== null ? (
                  <div><dt>신뢰도</dt><dd>{Math.round(selectedNode.confidence * 100)}%</dd></div>
                ) : null}
                {selectedNode.is_also_material_id ? (
                  <div><dt>겸 원재료</dt><dd className={styles.mono}>{selectedNode.is_also_material_id}</dd></div>
                ) : null}
              </dl>

              <h3 className={styles.neighborsTitle}>주변 노드 ({incidentEdges.length})</h3>
              {incidentEdges.length === 0 ? (
                <p className={styles.hint}>인접 관계가 없습니다. ⟳ 버튼으로 확장해 보세요.</p>
              ) : (
                <div className={styles.neighbors}>
                  {Object.entries(neighborGroups).map(([etype, items]) => (
                    <div key={etype} className={styles.neighborGroup}>
                      <div className={styles.groupLabel}>{EDGE_LABEL[etype] ?? etype}</div>
                      {items.map(({ neighbor, edge }, i) => (
                        <button
                          key={`${neighbor.id}-${i}`}
                          type="button"
                          className={styles.neighbor}
                          onClick={() => onNodeSelect(neighbor.id)}
                        >
                          <div className={styles.neighborRow}>
                            <span className={`${styles.badge} ${styles[`badge_${neighbor.node_type}`] ?? ""}`}>
                              {{ company: "기업", industry: "산업", product: "제품", raw_material: "원재료", segment: "부문" }[neighbor.node_type]}
                            </span>
                            <span className={styles.neighborName}>{neighbor.korean_name}</span>
                            {edge.share !== null ? (
                              <span className={styles.share}>{Math.round(edge.share * 100)}%</span>
                            ) : null}
                          </div>
                          {edge.source_quote ? (
                            <p className={styles.quote}>“{edge.source_quote}”</p>
                          ) : null}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className={styles.hint}>노드를 선택하면 상세와 주변 노드가 표시됩니다.</p>
          )}
        </aside>
      </div>
    </div>
  );
}