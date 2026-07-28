"""BusinessOntologyPort 구현 — business_ontology 패키지 래핑.

business_ontology.get_ontology() 가 온톨로지 YAML 을 로드·스키마 검증해 캐시(프로세스 단일).
여기서만 business_ontology 패키지를 직접 import 한다(import-linter business-ontology-behind-port).
패키지 객체(Resolution/IndustryNode/ProductNode/...)를 포트 DTO 로 변환해 반환.
엣지 인스턴스(그래프)는 DB 영속 데이터이므로 이 어댑터가 아니라 서비스가 DB 에서 읽는다.
"""

from __future__ import annotations

from app.ports.business_ontology import (
    BusinessNodeType,
    BusinessNormalizeResult,
    EdgeTypeOut,
    IndustryNodeOut,
    NodeOut,
)
from business_ontology import (
    BusinessOntology,
    CompanyNode,
    IndustryNode,
    Normalizer,
    ProductNode,
    RawMaterialNode,
    SegmentNode,
    get_ontology,
)

_ONT: BusinessOntology | None = None
_NORMALIZER: Normalizer | None = None


def _ensure() -> tuple[BusinessOntology, Normalizer]:
    """온톨로지·정규화기를 지연 1회 로드(모듈 전역 캐시)."""
    global _ONT, _NORMALIZER
    if _ONT is None:
        _ONT = get_ontology()  # 스키마 검증 포함, lru_cache 내부 캐시
        _NORMALIZER = Normalizer(_ONT)
    assert _ONT is not None and _NORMALIZER is not None
    return _ONT, _NORMALIZER


class BusinessOntologyAdapter:
    """BusinessOntologyPort 구현. 상태 없는 thin wrapper(온톨로지는 정적 데이터)."""

    def resolve(
        self, raw: str, node_type: BusinessNodeType, standard: str | None = None
    ) -> BusinessNormalizeResult:
        _ont, norm = _ensure()
        r = norm.resolve(raw, node_type, standard=standard)
        return _resolution_out(r)

    def resolve_many(
        self,
        mentions: list[tuple[str, BusinessNodeType]],
        standard: str | None = None,
    ) -> list[BusinessNormalizeResult]:
        _ont, norm = _ensure()
        return [_resolution_out(r) for r in norm.resolve_many(mentions, standard=standard)]

    def list_industries(self) -> list[IndustryNodeOut]:
        ont, _norm = _ensure()
        return [_industry_out(n) for n in ont.industries.values()]

    def industry(self, gics_code_or_id: str) -> IndustryNodeOut | None:
        ont, _norm = _ensure()
        # ID 직접 → GICS 코드 경로 모두 시도.
        node = ont.industries.get(gics_code_or_id)
        if node is None:
            node_id = ont.by_gics_code.get(gics_code_or_id)
            if node_id is not None:
                node = ont.industries.get(node_id)
        return _industry_out(node) if node else None

    def list_edge_types(self) -> list[EdgeTypeOut]:
        ont, _norm = _ensure()
        return [_edge_type_out(e) for e in ont.edge_types.values()]

    def list_nodes(self, node_type: BusinessNodeType | None = None) -> list[NodeOut]:
        ont, _norm = _ensure()
        out: list[NodeOut] = []
        if node_type in (None, "product"):
            out.extend(_product_out(n) for n in ont.products.values())
        if node_type in (None, "raw_material"):
            out.extend(_material_out(n) for n in ont.materials.values())
        if node_type in (None, "company"):
            out.extend(_company_out(n) for n in ont.companies.values())
        if node_type in (None, "segment"):
            out.extend(_segment_out(n) for n in ont.segments.values())
        if node_type in (None, "industry"):
            out.extend(_industry_node_out(n) for n in ont.industries.values())
        return out

    def node(self, node_id: str) -> NodeOut | None:
        ont, _norm = _ensure()
        if node_id in ont.products:
            return _product_out(ont.products[node_id])
        if node_id in ont.materials:
            return _material_out(ont.materials[node_id])
        if node_id in ont.companies:
            return _company_out(ont.companies[node_id])
        if node_id in ont.segments:
            return _segment_out(ont.segments[node_id])
        if node_id in ont.industries:
            return _industry_node_out(ont.industries[node_id])
        return None

    def issue_canonical(self, node_type: BusinessNodeType, name: str) -> str:
        _ont, norm = _ensure()
        return norm.auto_canonical_id(node_type, name)


def _resolution_out(r) -> BusinessNormalizeResult:  # r: business_ontology.Resolution
    return BusinessNormalizeResult(
        term=r.term,
        node_type=r.node_type,
        canonical_id=r.canonical_id,
        matched_via=r.matched_via,
        status=r.status,
        confidence=r.confidence,
    )


def _industry_out(n: IndustryNode) -> IndustryNodeOut:
    return IndustryNodeOut(
        id=n.id,
        gics_code=n.gics_code,
        gics_sector=n.gics_sector,
        gics_group=n.gics_group,
        gics_industry=n.gics_industry,
        gics_sub_industry=n.gics_sub_industry,
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
    )


def _industry_node_out(n: IndustryNode) -> NodeOut:
    return NodeOut(
        id=n.id,
        node_type="industry",
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
    )


def _product_out(n: ProductNode) -> NodeOut:
    return NodeOut(
        id=n.id,
        node_type="product",
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
        commodity_type=n.commodity_type,
        is_also_material_id=n.is_also_material_id,
    )


def _material_out(n: RawMaterialNode) -> NodeOut:
    return NodeOut(
        id=n.id,
        node_type="raw_material",
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
        commodity_ref=dict(n.commodity_ref),
    )


def _company_out(n: CompanyNode) -> NodeOut:
    return NodeOut(
        id=n.id,
        node_type="company",
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
        corp_code=n.corp_code,
        stock_code=n.stock_code,
    )


def _segment_out(n: SegmentNode) -> NodeOut:
    return NodeOut(
        id=n.id,
        node_type="segment",
        korean_name=n.korean_name,
        english_name=n.english_name,
        aliases=list(n.aliases),
        segment_type=n.segment_type,
    )


def _edge_type_out(e) -> EdgeTypeOut:  # e: business_ontology.EdgeTypeMeta
    return EdgeTypeOut(
        id=e.id,
        korean_name=e.korean_name,
        english_name=e.english_name,
        directed=e.directed,
        description=e.description,
    )
