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
import dagre from "dagre";
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

const NODE_W = 184;
const NODE_H = 68;

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

function dagreLayout(
  ids: string[],
  edges: { source: string; target: string }[],
): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", ranksep: 90, nodesep: 28, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const id of ids) g.setNode(id, { width: NODE_W, height: NODE_H });
  for (const e of edges) {
    if (ids.includes(e.source) && ids.includes(e.target)) g.setEdge(e.source, e.target);
  }
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const id of ids) {
    const p = g.node(id);
    pos[id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
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
  const ids = useMemo(() => nodes.map((n) => n.id), [nodes]);
  const layoutEdges = useMemo(
    () => edges.map((e) => ({ source: e.src, target: e.dst })),
    [edges],
  );
  const positions = useMemo(() => dagreLayout(ids, layoutEdges), [ids, layoutEdges]);

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