"use client";

import { memo, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { BusinessExploreNode, BusinessLayer } from "@/lib/types";

import styles from "./page.module.css";

export const LAYER_LABEL: Record<BusinessLayer, string> = {
  company: "기업",
  industry: "산업",
  product: "제품",
  raw_material: "원재료",
  segment: "부문",
};

const EDGE_LABEL: Record<string, string> = {
  manufactures: "생산",
  uses_material: "원재료",
  operates_in: "운영",
  competes_with: "경쟁",
  supplies: "공급",
  supplies_to: "납품",
  has_segment: "부문",
  part_of_value_chain: "가치사슬",
  sibling_industry: "형제산업",
  is_also_material: "겸원재료",
  parent_of: "상위",
  subsidiary_of: "자회사",
};

const NODE_W = 176;
const NODE_H = 60;
const DX = 206;
const DY = 78;
const MAX_COLS = 6;
const BAND_GAP = 30;

const LAYER_ORDER: BusinessLayer[] = [
  "company",
  "industry",
  "product",
  "raw_material",
  "segment",
];

interface GraphNodeData {
  node: BusinessExploreNode;
  isFocal: boolean;
  isSel: boolean;
  onExpand: (id: string) => void;
  onSelect: (id: string) => void;
  [key: string]: unknown;
}

type OntologyNode = Node<GraphNodeData, "ontology">;
type OntologyNodeProps = NodeProps<OntologyNode>;

// focal 을 중심(0,0)에 두고, 이웃을 업스트림(좌)/다운스트림(우)으로 분리한 뒤
// node_type(계층)별 가로 밴드로 격자 배치. 005930 처럼 이웃이 한쪽으로 쏠려도
// 1열 장칼럼 이 아닌 계층별 격자가 되어 fitView 후에도 읽기 가능.
function structuredLayout(
  nodes: BusinessExploreNode[],
  edges: { source: string; target: string }[],
  focalId: string,
): Record<string, { x: number; y: number }> {
  const pos: Record<string, { x: number; y: number }> = {};
  pos[focalId] = { x: -NODE_W / 2, y: -NODE_H / 2 };

  // focal 기준 방향: out(focal 이 src) 이 in 보다 우선.
  const dir = new Map<string, "in" | "out">();
  for (const e of edges) {
    if (e.target === focalId && !dir.has(e.source)) dir.set(e.source, "in");
  }
  for (const e of edges) {
    if (e.source === focalId) dir.set(e.target, "out");
  }

  const neighbors = nodes.filter((n) => n.id !== focalId);
  const groups = new Map<string, BusinessExploreNode[]>();
  for (const n of neighbors) {
    const d = dir.get(n.id) ?? "out";
    const k = `${d}:${n.node_type}`;
    const arr = groups.get(k);
    if (arr) arr.push(n);
    else groups.set(k, [n]);
  }

  const bandTypes = LAYER_ORDER.filter((t) =>
    neighbors.some((n) => n.node_type === t),
  );
  if (bandTypes.length === 0) return pos;

  const bandRows = bandTypes.map((t) => {
    const outN = groups.get(`out:${t}`)?.length ?? 0;
    const inN = groups.get(`in:${t}`)?.length ?? 0;
    return Math.max(Math.ceil(outN / MAX_COLS), Math.ceil(inN / MAX_COLS), 1);
  });
  const bandHeights = bandRows.map((r) => r * DY);
  const totalH =
    bandHeights.reduce((a, b) => a + b, 0) + (bandTypes.length - 1) * BAND_GAP;

  const bandCenters: number[] = [];
  let cursor = -totalH / 2;
  bandTypes.forEach((_, i) => {
    bandCenters.push(cursor + bandHeights[i] / 2);
    cursor += bandHeights[i] + BAND_GAP;
  });

  bandTypes.forEach((t, bi) => {
    const cy = bandCenters[bi];
    const rows = bandRows[bi];
    (["out", "in"] as const).forEach((d) => {
      const arr = groups.get(`${d}:${t}`);
      if (!arr) return;
      arr.forEach((n, i) => {
        const col = i % MAX_COLS;
        const row = Math.floor(i / MAX_COLS);
        const yOff = (row - (rows - 1) / 2) * DY;
        const xBase = (col + 1) * DX;
        const x = d === "out" ? xBase - NODE_W / 2 : -xBase - NODE_W / 2;
        pos[n.id] = { x, y: cy + yOff - NODE_H / 2 };
      });
    });
  });
  return pos;
}

const OntologyNodeCard = memo(function OntologyNodeCard({ data }: OntologyNodeProps) {
  const { node, isFocal, isSel, onExpand, onSelect } = data;
  return (
    <div
      className={`${styles.node} ${styles[`layer_${node.node_type}`] ?? ""} ${
        isFocal ? styles.nodeFocal : ""
      } ${isSel ? styles.nodeSel : ""}`}
      onClick={() => onSelect(node.id)}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <div className={styles.nodeBody}>
        <span className={`${styles.badge} ${styles[`badge_${node.node_type}`] ?? ""}`}>
          {LAYER_LABEL[node.node_type]}
        </span>
        <span className={styles.nodeName} title={node.korean_name}>
          {node.korean_name}
        </span>
      </div>
      <button
        type="button"
        className={styles.expandBtn}
        aria-label="이웃 펼치기"
        onClick={(e) => {
          e.stopPropagation();
          onExpand(node.id);
        }}
      >
        ⟳
      </button>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
});

const nodeTypes: NodeTypes = { ontology: OntologyNodeCard };

interface OntologyGraphProps {
  nodes: BusinessExploreNode[];
  edges: { src: string; dst: string; edge_type: string; share: number | null }[];
  focalId: string;
  selectedId: string | null;
  onNodeExpand: (id: string) => void;
  onNodeSelect: (id: string) => void;
}

export default function OntologyGraph({
  nodes,
  edges,
  focalId,
  selectedId,
  onNodeExpand,
  onNodeSelect,
}: OntologyGraphProps) {
  const layoutEdges = useMemo(
    () => edges.map((e) => ({ source: e.src, target: e.dst })),
    [edges],
  );
  const positions = useMemo(
    () => structuredLayout(nodes, layoutEdges, focalId),
    [nodes, layoutEdges, focalId],
  );

  const rfNodes: OntologyNode[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id,
        type: "ontology",
        position: positions[n.id] ?? { x: 0, y: 0 },
        data: {
          node: n,
          isFocal: n.id === focalId,
          isSel: n.id === selectedId,
          onExpand: onNodeExpand,
          onSelect: onNodeSelect,
        },
      })),
    [nodes, positions, focalId, selectedId, onNodeExpand, onNodeSelect],
  );

  const rfEdges: Edge[] = useMemo(
    () =>
      edges.map((e, i) => ({
        id: `${e.src}|${e.dst}|${e.edge_type}|${i}`,
        source: e.src,
        target: e.dst,
        label: `${EDGE_LABEL[e.edge_type] ?? e.edge_type}${e.share !== null ? ` ${Math.round(e.share * 100)}%` : ""}`,
        animated: e.edge_type === "part_of_value_chain",
        style: { stroke: "var(--border)", strokeWidth: 1.5 },
        labelStyle: { fontSize: 11, fill: "var(--muted)" },
      })),
    [edges],
  );

  return (
    <div className={styles.canvas}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => {
            const d = n.data as unknown as GraphNodeData | undefined;
            const nt = d?.node?.node_type;
            const colors: Record<BusinessLayer, string> = {
              company: "#2b6cb0",
              industry: "#2f855a",
              product: "#c05621",
              raw_material: "#8b5e3c",
              segment: "#718096",
            };
            return (nt && colors[nt]) ?? "#718096";
          }}
        />
      </ReactFlow>
    </div>
  );
}