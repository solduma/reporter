"""그래프 — 엣지 인스턴스 위의 순회·가치사슬 walk.

financial_ontology.graph 와 달리 엣지가 온톨로지 SOT가 아니라 인스턴스 데이터이므로,
Graph 는 노드 사전(ontology) + 엣지 리스트 로 구성된다.
"""

from __future__ import annotations

from collections import defaultdict

from .models import BusinessOntology, Edge, EdgeType


class Graph:
    def __init__(self, ontology: BusinessOntology, edges: list[Edge] | None = None):
        self._ont = ontology
        self._edges: list[Edge] = list(edges or [])
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            self._out[e.src].append(e)
            self._in[e.dst].append(e)

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges)

    def out_edges(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e for e in self._out.get(node_id, []) if edge_type is None or e.edge_type == edge_type
        ]

    def in_edges(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e for e in self._in.get(node_id, []) if edge_type is None or e.edge_type == edge_type
        ]

    def neighbors(self, node_id: str, edge_type: EdgeType | None = None) -> list[str]:
        return [e.dst for e in self.out_edges(node_id, edge_type)]

    def manufactures(self, company_id: str) -> list[tuple[Edge, str]]:
        """회사가 생산하는 제품 — (엣지, product canonical_id)."""
        return [(e, e.dst) for e in self.out_edges(company_id, "manufactures")]

    def materials_of(self, company_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.dst) for e in self.out_edges(company_id, "uses_material")]

    def operates_in(self, company_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.dst) for e in self.out_edges(company_id, "operates_in")]

    def segments_of(self, company_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.dst) for e in self.out_edges(company_id, "has_segment")]

    def competitors(self, company_id: str) -> list[str]:
        # competes_with 가 무방향처럼 취급되도록 양방향 수집.
        out = {e.dst for e in self.out_edges(company_id, "competes_with")}
        inb = {e.src for e in self.in_edges(company_id, "competes_with")}
        return sorted(out | inb)

    def customers(self, company_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.dst) for e in self.out_edges(company_id, "supplies_to")]

    def suppliers(self, company_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.src) for e in self.in_edges(company_id, "supplies_to")]

    def value_chain(self, company_id: str) -> dict[str, list[Edge]]:
        """part_of_value_chain 엣지를 chain_stage 별로 그룹화."""
        stages: dict[str, list[Edge]] = defaultdict(list)
        for e in self.out_edges(company_id, "part_of_value_chain"):
            stage = e.chain_stage or "unspecified"
            stages[stage].append(e)
        return dict(stages)

    def peers_by_industry(self, company_id: str) -> list[str]:
        """같은 GICS sub-industry 에 operates_in 하는 다른 회사들 — 스크리너 동종업 비교용."""
        industries = {e.dst for e in self.out_edges(company_id, "operates_in")}
        if not industries:
            return []
        peers: set[str] = set()
        for ind in industries:
            for e in self.in_edges(ind, "operates_in"):
                if e.src != company_id:
                    peers.add(e.src)
        return sorted(peers)
