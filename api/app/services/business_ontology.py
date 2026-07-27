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


# ── 노드 중심 횡단 탐색(cross-stock 1-hop) ─────────────────────────────────
def _focal_dict(node_id: str, db: Session) -> dict[str, object] | None:
    """canonical_id → focal 노드 dict(정적 메타 + 인스턴스 status/confidence 병합).

    정적 온톨로지(port.node / port.industry)가 aliases·commodity·gics_code 등 메타를 제공하고,
    DB 인스턴스 행이 status·confidence·stock_code 를 제공. 어느 한쪽이라도 있으면 focal 구성.
    """
    static_node = _port().node(node_id)
    static_ind = _port().industry(node_id)
    if static_node is None and static_ind is None:
        # 정적 미해결 — DB 에 pending_review 인스턴스만 있을 수 있으나 탐색은 정준 기준.
        rows = db.scalars(
            select(BusinessOntologyNode).where(BusinessOntologyNode.canonical_id == node_id)
        ).all()
        if not rows:
            return None
        r = rows[0]
        return {
            "id": node_id,
            "node_type": r.node_type,
            "korean_name": r.korean_name,
            "english_name": r.english_name or "",
            "aliases": [],
            "status": r.status,
            "confidence": r.confidence,
            "commodity_type": None,
            "is_also_material_id": None,
            "gics_code": None,
            "stock_code": r.stock_code if r.node_type == "company" else None,
        }

    rows = db.scalars(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.canonical_id == node_id,
            BusinessOntologyNode.status == "canonical",
        )
    ).all()
    # 인스턴스 status/confidence 집계 — 정준 행 중 최고 confidence.
    status = rows[0].status if rows else "canonical"
    confidence = max((r.confidence or 0.0) for r in rows) if rows else None
    stock_code = None
    if rows and rows[0].node_type == "company":
        stock_code = rows[0].stock_code

    if static_ind is not None:
        return {
            "id": static_ind.id,
            "node_type": "industry",
            "korean_name": static_ind.korean_name,
            "english_name": static_ind.english_name,
            "aliases": list(static_ind.aliases),
            "status": status,
            "confidence": confidence,
            "commodity_type": None,
            "is_also_material_id": None,
            "gics_code": static_ind.gics_code,
            "stock_code": stock_code,
        }
    n = static_node  # type: ignore[assignment]
    return {
        "id": n.id,
        "node_type": n.node_type,
        "korean_name": n.korean_name,
        "english_name": n.english_name,
        "aliases": list(n.aliases),
        "status": status,
        "confidence": confidence,
        "commodity_type": n.commodity_type,
        "is_also_material_id": n.is_also_material_id,
        "gics_code": None,
        "stock_code": n.stock_code if n.node_type == "company" else stock_code,
    }


def _neighbor_dict(
    n: BusinessOntologyNode,
    edge: BusinessOntologyEdge | None,
    direction: str,
    edge_type: str,
) -> dict[str, object]:
    """인스턴스 이웃 노드 + 엣지 메타 → neighbor dict."""
    cid = n.canonical_id or f"{n.node_type}:{n.korean_name}"
    d: dict[str, object] = {
        "id": cid,
        "node_type": n.node_type,
        "korean_name": n.korean_name,
        "english_name": n.english_name or "",
        "aliases": [],
        "status": n.status,
        "confidence": n.confidence,
        "commodity_type": None,
        "is_also_material_id": None,
        "gics_code": None,
        "stock_code": n.stock_code if n.node_type == "company" else None,
        "edge_type": edge_type,
        "direction": direction,
    }
    if edge is not None:
        d.update(
            {
                "share": edge.share,
                "period": edge.period or None,
                "source_quote": edge.source_quote,
                "chain_stage": edge.chain_stage,
            }
        )
    else:
        d.update(
            {
                "share": None,
                "period": None,
                "source_quote": None,
                "chain_stage": None,
            }
        )
    return d


def explore_node(db: Session, node_id: str) -> dict[str, object] | None:
    """노드 중심 1-hop 탐색 — focal 의 모든 stock_code 인스턴스를 모아 cross-stock 이웃 구성.

    회사·제품·원재료·산업 어느 노드든 focal 이 될 수 있다. 엣지는 company-centric(src=회사)이므로
    비-회사 focal 의 이웃은 역방향 엣지로 수집. 산업 focal 은 GICS 형제 sub-industry 를 합성 이웃으로
    추가하고, 제품/원재료 focal 은 is_also_material_id 교차링크를 추가한다.
    """
    focal = _focal_dict(node_id, db)
    if focal is None:
        return None

    # focal 의 모든 정준 인스턴스 PK — 동일 canonical_id 가 여러 stock_code 에 걸쳐 존재.
    pks = [
        r.id
        for r in db.scalars(
            select(BusinessOntologyNode).where(
                BusinessOntologyNode.canonical_id == node_id,
                BusinessOntologyNode.status == "canonical",
            )
        ).all()
    ]
    neighbors: list[dict[str, object]] = []
    edges_out: list[dict[str, object]] = []
    if pks:
        rows = db.scalars(
            select(BusinessOntologyEdge).where(
                (BusinessOntologyEdge.src_node_id.in_(pks))
                | (BusinessOntologyEdge.dst_node_id.in_(pks))
            )
        ).all()
        # 이웃 노드 PK 수집(focal 아닌 끝).
        other_pks: set[int] = set()
        for e in rows:
            if e.src_node_id in pks and e.dst_node_id not in pks:
                other_pks.add(e.dst_node_id)
            elif e.dst_node_id in pks and e.src_node_id not in pks:
                other_pks.add(e.src_node_id)
        nbr_nodes = {
            n.id: n
            for n in db.scalars(
                select(BusinessOntologyNode).where(BusinessOntologyNode.id.in_(other_pks))
            ).all()
        }
        for e in rows:
            if e.src_node_id in pks and e.dst_node_id not in pks:
                nbr = nbr_nodes.get(e.dst_node_id)
                if nbr is None:
                    continue
                neighbors.append(_neighbor_dict(nbr, e, "out", e.edge_type))
                edges_out.append(
                    {"src": node_id, "dst": nbr.canonical_id or f"{nbr.node_type}:{nbr.korean_name}", "edge_type": e.edge_type,
                     "share": e.share, "period": e.period or None, "source_quote": e.source_quote,
                     "chain_stage": e.chain_stage, "confidence": e.confidence}
                )
            elif e.dst_node_id in pks and e.src_node_id not in pks:
                nbr = nbr_nodes.get(e.src_node_id)
                if nbr is None:
                    continue
                neighbors.append(_neighbor_dict(nbr, e, "in", e.edge_type))
                edges_out.append(
                    {"src": nbr.canonical_id or f"{nbr.node_type}:{nbr.korean_name}", "dst": node_id, "edge_type": e.edge_type,
                     "share": e.share, "period": e.period or None, "source_quote": e.source_quote,
                     "chain_stage": e.chain_stage, "confidence": e.confidence}
                )

    # 산업 focal — GICS 형제 sub-industry 합성 이웃(같은 6자리 industry).
    if focal["node_type"] == "industry" and focal.get("gics_code"):
        gics = str(focal["gics_code"])
        prefix6 = gics[:6]
        for ind in _port().list_industries():
            if ind.gics_code == gics or not ind.gics_code.startswith(prefix6):
                continue
            nbr_id = ind.id
            neighbors.append(
                {
                    "id": nbr_id, "node_type": "industry", "korean_name": ind.korean_name,
                    "english_name": ind.english_name, "aliases": list(ind.aliases),
                    "status": "canonical", "confidence": None, "commodity_type": None,
                    "is_also_material_id": None, "gics_code": ind.gics_code, "stock_code": None,
                    "edge_type": "sibling_industry", "direction": "out",
                    "share": None, "period": None, "source_quote": None, "chain_stage": None,
                }
            )
            edges_out.append(
                {"src": node_id, "dst": nbr_id, "edge_type": "sibling_industry",
                 "share": None, "period": None, "source_quote": None, "chain_stage": None, "confidence": None}
            )

    # 제품/원재료 focal — is_also_material_id 교차링크 합성 이웃.
    cross = focal.get("is_also_material_id")
    if cross and cross != node_id:
        cross_static = _port().node(cross)
        if cross_static is not None:
            neighbors.append(
                {
                    "id": cross_static.id, "node_type": cross_static.node_type,
                    "korean_name": cross_static.korean_name, "english_name": cross_static.english_name,
                    "aliases": list(cross_static.aliases), "status": "canonical", "confidence": None,
                    "commodity_type": cross_static.commodity_type, "is_also_material_id": None,
                    "gics_code": None, "stock_code": None,
                    "edge_type": "is_also_material", "direction": "out",
                    "share": None, "period": None, "source_quote": None, "chain_stage": None,
                }
            )
            edges_out.append(
                {"src": node_id, "dst": cross_static.id, "edge_type": "is_also_material",
                 "share": None, "period": None, "source_quote": None, "chain_stage": None, "confidence": None}
            )

    return {"focal": focal, "neighbors": neighbors, "edges": edges_out}
