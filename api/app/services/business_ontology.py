"""비즈니스 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

정적 온톨로지(정규화·GICS 트리·노드/엣지타입 메타)는 BusinessOntologyPort(get_business_ontology_port)
경유로 어댑터(패키지) 호출. 엣지 인스턴스 그래프·부문 매출은 DB 영속 데이터(business_ontology_node/edge,
segment_sales 테이블 — Task #28 생성)를 서비스가 직접 읽는다(포트는 정적 데이터만 담당).
계층 방향(routers → services → adapters/db)을 지키기 위한 thin 서비스.
"""

from __future__ import annotations

from dataclasses import asdict

from app.ports.business_ontology import (
    BusinessNodeType,
    BusinessNormalizeResult,
    EdgeTypeOut,
    IndustryNodeOut,
    NodeOut,
)


def _port():
    from app.adapters.business_ontology import get_business_ontology_port

    return get_business_ontology_port()


# ── 정적 온톨로지(포트 경유) ──────────────────────────────────────────────
def normalize(
    mentions: list[tuple[str, BusinessNodeType]], standard: str | None = None
) -> list[BusinessNormalizeResult]:
    """raw mention (name, node_type) 목록 → 정준 ID 일괄 해석."""
    return _port().resolve_many(mentions, standard=standard)


def normalize_one(
    raw: str, node_type: BusinessNodeType, standard: str | None = None
) -> BusinessNormalizeResult:
    return _port().resolve(raw, node_type, standard=standard)


def industries() -> list[IndustryNodeOut]:
    return _port().list_industries()


def industry(gics_code_or_id: str) -> IndustryNodeOut | None:
    return _port().industry(gics_code_or_id)


def edge_types() -> list[EdgeTypeOut]:
    return _port().list_edge_types()


def nodes(node_type: BusinessNodeType | None = None) -> list[NodeOut]:
    return _port().list_nodes(node_type=node_type)


def node(node_id: str) -> NodeOut | None:
    return _port().node(node_id)


# ── 회사 그래프·부문 매출(DB 영속 — Task #28 에서 ORM 모델·쿼리 주입) ──────
# v1 스켈레톤 단계에서는 테이블이 비어있으므로 빈 결과를 반환한다. ingest 파이프라인
# (business_ingest.py LLM 추출 스텝 + segment_sales fetch)가 행을 채운 뒤 실데이터 반환.


def company_graph(db, code: str) -> dict[str, object]:
    """회사 노드 + 인접 엣지/이웃 노드. Task #28 에서 business_ontology_node/edge 쿼리로 채운다."""
    return {"nodes": [], "edges": []}


def company_segments(db, code: str, year: str | None = None) -> list[dict[str, object]]:
    """부문별 매출(iotHom3MdQe). Task #28 에서 segment_sales 테이블 쿼리로 채운다."""
    return []


def company_products(db, code: str) -> list[dict[str, object]]:
    """회사가 생산하는 제품 + 매출 비중(manufactures 엣지). Task #28 에서 채운다."""
    return []


def company_materials(db, code: str) -> list[dict[str, object]]:
    """회사가 사용하는 원재료(uses_material 엣지). Task #28 에서 채운다."""
    return []


def industry_companies(db, gics_code: str) -> list[dict[str, object]]:
    """GICS 동종업(peers). Task #28 에서 operates_in 엣지 역방향 쿼리로 채운다."""
    return []


def node_to_dict(n: NodeOut | IndustryNodeOut) -> dict[str, object]:
    """포트 DTO → 응답 dict(라우터/스키마 변환 공통)."""
    return {k: v for k, v in asdict(n).items() if v not in (None, [], {})} or asdict(n)
