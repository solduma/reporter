"""비즈니스 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

정적 온톨로지(정규화·GICS 트리·노드/엣지타입 메타)는 BusinessOntologyPort(get_business_ontology_port)
경유로 어댑터(패키지) 호출. 엣지 인스턴스 그래프·부문 매출은 DB 영속 데이터(business_ontology_node/edge,
segment_sales 테이블 — Task #28 생성)를 서비스가 직접 읽는다(포트는 정적 데이터만 담당).
계층 방향(routers → services → adapters/db)을 지키기 위한 thin 서비스.
"""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BusinessOntologyEdge,
    BusinessOntologyNode,
    CorpCodeMap,
    SegmentSales,
)
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


# ── 회사 해석(포트 + DB) ──────────────────────────────────────────────────
def resolve_company(db: Session, name: str) -> BusinessNormalizeResult:
    """회사명 → 정준 ID. 패키지 시드 사전 → CorpCodeMap DB exact → pending_review.

    패키지 normalizer 가 접두/접미사 제거한 term 을 그대로 CorpCodeMap exact 에 재사용(중복 strip 회피).
    정준 매칭 시 canonical_id = CMP_KRX_<stock_code>. DB fuzzy 는 v2.
    """
    port = _port()
    r = port.resolve(name, "company")
    if r.resolved:
        return r
    cleaned = r.term or name  # 패키지가 suffix 제거한 결과
    row = db.execute(select(CorpCodeMap.stock_code).where(CorpCodeMap.corp_name == cleaned)).first()
    if row:
        return BusinessNormalizeResult(
            term=name,
            node_type="company",
            canonical_id=f"CMP_KRX_{row.stock_code}",
            matched_via="corp_name",
            status="canonical",
            confidence=1.0,
        )
    return r  # 패키지의 pending_review 결과 그대로


# ── 회사 그래프·부문 매출(DB 영속) ────────────────────────────────────────
def _node_ref(n: BusinessOntologyNode) -> str:
    """그래프 응답에서 노드를 식별하는 문자열 — 정준 ID 우선, 없으면 타입:이름."""
    return n.canonical_id or f"{n.node_type}:{n.korean_name}"


def company_graph(db: Session, code: str) -> dict[str, object]:
    """회사 비즈니스 그래프 — 노드(business_ontology_node) + 엣지(business_ontology_edge)."""
    nodes = db.scalars(
        select(BusinessOntologyNode).where(BusinessOntologyNode.stock_code == code)
    ).all()
    if not nodes:
        return {"nodes": [], "edges": []}
    by_pk = {n.id: n for n in nodes}
    node_dicts: list[dict[str, object]] = []
    seen: set[str] = set()
    for n in nodes:
        ref = _node_ref(n)
        if ref in seen:
            continue
        seen.add(ref)
        node_dicts.append(
            {
                "id": ref,
                "node_type": n.node_type,
                "korean_name": n.korean_name,
                "english_name": n.english_name or "",
                "aliases": [],
                "status": n.status,
                "confidence": n.confidence,
            }
        )
    edges = db.scalars(
        select(BusinessOntologyEdge).where(BusinessOntologyEdge.stock_code == code)
    ).all()
    edge_dicts: list[dict[str, object]] = []
    for e in edges:
        src = by_pk.get(e.src_node_id)
        dst = by_pk.get(e.dst_node_id)
        if not src or not dst:
            continue
        edge_dicts.append(
            {
                "src": _node_ref(src),
                "dst": _node_ref(dst),
                "edge_type": e.edge_type,
                "share": e.share,
                "period": e.period or None,
                "source_quote": e.source_quote,
                "chain_stage": e.chain_stage,
                "confidence": e.confidence,
            }
        )
    return {"nodes": node_dicts, "edges": edge_dicts}


def company_segments(db: Session, code: str, year: str | None = None) -> list[dict[str, object]]:
    """부문별 매출(iotHom3MdQe → segment_sales). year 미지정시 전체."""
    stmt = select(SegmentSales).where(SegmentSales.stock_code == code)
    if year:
        stmt = stmt.where(SegmentSales.bsns_year == year)
    rows = db.scalars(stmt).all()
    return [
        {
            "bsns_year": r.bsns_year,
            "report_code": r.report_code,
            "segment_type": r.segment_type,
            "segment_name": r.segment_name,
            "revenue": r.revenue,
            "ratio_pct": r.ratio_pct,
        }
        for r in rows
    ]


def _company_edges(db: Session, code: str, edge_type: str) -> list[dict[str, object]]:
    """회사→대상 엣지 + 대상 노드 메타(manufactures/uses_material 공용)."""
    edges = db.scalars(
        select(BusinessOntologyEdge).where(
            BusinessOntologyEdge.stock_code == code,
            BusinessOntologyEdge.edge_type == edge_type,
        )
    ).all()
    if not edges:
        return []
    dst_ids = [e.dst_node_id for e in edges]
    nodes = {
        n.id: n
        for n in db.scalars(
            select(BusinessOntologyNode).where(BusinessOntologyNode.id.in_(dst_ids))
        ).all()
    }
    out: list[dict[str, object]] = []
    for e in edges:
        dst = nodes.get(e.dst_node_id)
        if dst is None:
            continue
        out.append(
            {
                "node_id": dst.canonical_id or _node_ref(dst),
                "korean_name": dst.korean_name,
                "edge_type": e.edge_type,
                "share": e.share,
                "period": e.period or None,
                "confidence": e.confidence,
            }
        )
    return out


def company_products(db: Session, code: str) -> list[dict[str, object]]:
    """회사가 생산하는 제품 + 매출 비중(manufactures 엣지)."""
    return _company_edges(db, code, "manufactures")


def company_materials(db: Session, code: str) -> list[dict[str, object]]:
    """회사가 사용하는 원재료(uses_material 엣지)."""
    return _company_edges(db, code, "uses_material")


def industry_companies(db: Session, gics_code: str) -> list[dict[str, object]]:
    """GICS 동종업 종목(peers) — operates_in 엣지 역방향. 산업 노드는 GICS 코드 또는 ID 로 식별."""
    ind = industry(gics_code)
    if ind is None:
        return []
    # 해당 산업을 operates_in 하는 회사 노드 → 회사 메타(node_type=company 동일 stock_code).
    ind_nodes = db.scalars(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.node_type == "industry",
            BusinessOntologyNode.canonical_id == ind.id,
        )
    ).all()
    if not ind_nodes:
        return []
    codes = [n.stock_code for n in ind_nodes]
    companies = {
        c.stock_code: c
        for c in db.scalars(
            select(BusinessOntologyNode).where(
                BusinessOntologyNode.node_type == "company",
                BusinessOntologyNode.stock_code.in_(codes),
            )
        ).all()
    }
    out: list[dict[str, object]] = []
    for sc in codes:
        c = companies.get(sc)
        out.append(
            {
                "stock_code": sc,
                "korean_name": c.korean_name if c else "",
                "canonical_id": c.canonical_id if c else None,
            }
        )
    return out


def node_to_dict(n: NodeOut | IndustryNodeOut) -> dict[str, object]:
    """포트 DTO → 응답 dict(라우터/스키마 변환 공통)."""
    return {k: v for k, v in asdict(n).items() if v not in (None, [], {})} or asdict(n)
