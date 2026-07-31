"""비즈니스 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

정적 온톨로지(정규화·GICS 트리·노드/엣지타입 메타)는 BusinessOntologyPort(get_business_ontology_port)
경유로 어댑터(패키지) 호출. 엣지 인스턴스 그래프·부문 매출은 DB 영속 데이터(business_ontology_node/edge,
segment_sales 테이블 — Task #28 생성)를 서비스가 직접 읽는다(포트는 정적 데이터만 담당).
계층 방향(routers → services → adapters/db)을 지키기 위한 thin 서비스.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from sqlalchemy import func, select
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
from app.ports.llm import LLMError, LLMPort

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


def _port():
    from app.adapters.business_ontology import get_business_ontology_port

    return get_business_ontology_port()


def _get_llm(settings: Settings) -> LLMPort | None:
    from app.adapters.llm.factory import get_llm

    return get_llm(settings)


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
    """회사명 → 정준 ID. 패키지 시드 사전 → CorpCodeMap DB exact → 비상장 자동 new(CMP_GLOBAL_).

    패키지 normalizer 가 접두/접미사 제거한 term 을 그대로 CorpCodeMap exact 에 재사용(중복 strip 회피).
    상장사 정준 매칭 시 canonical_id = CMP_KRX_<stock_code>. 시드·상장 모두 아니면 비상장 글로벌사로
    이름 기반 CMP_GLOBAL_<slug> 자동 발급(회사 고유 엔티티 — 출처 stock_code 와 무관).
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
    if r.status == "rejected":
        return r  # 비엔티티 NER 오분류 — 자동 new 발급 안 함
    # 비상장 글로벌사 — 이름 기반 자동 new 발급(하만·eMagin 등). stock_code 무관 정체성.
    cid = port.issue_canonical("company", cleaned or name)
    return BusinessNormalizeResult(
        term=name,
        node_type="company",
        canonical_id=cid,
        matched_via="auto_new",
        status="canonical",
        confidence=0.7,
    )


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


# ── 비즈니스 인사이트(딥다이브 ③ 사업모델 보강용) ─────────────────────────────────

def industry_company_ratios(db: Session, gics_code: str) -> dict[str, float | None]:
    """GICS 산업 평균 PER·PBR·ROE — operates_in 하는 company 노드의 재무 데이터 기반.

    industry_companies 로 종목集合을 구하고, 각 종목의 최신 추정 아닌 재무비율을 취해
    산업 평균을 산출한다. 데이터 부족 시 None 을 반환(우아한 강등).
    """
    companies = industry_companies(db, gics_code)
    if not companies:
        return {"per": None, "pbr": None, "roe": None, "count": 0}
    codes = [c["stock_code"] for c in companies if c.get("stock_code")]
    if not codes:
        return {"per": None, "pbr": None, "roe": None, "count": 0}

    from app.db.models import Financial

    rows = db.scalars(
        select(Financial)
        .where(Financial.stock_code.in_(codes), Financial.is_estimate.is_(False))
        .order_by(Financial.period.desc())
    ).all()
    # 최신 period별 1건만(같은 period 여러 행 방지)
    latest: dict[str, Financial] = {}
    for r in rows:
        if r.stock_code not in latest:
            latest[r.stock_code] = r
    vals = list(latest.values())
    if not vals:
        return {"per": None, "pbr": None, "roe": None, "count": 0}

    def _avg(attr: str) -> float | None:
        nums = [getattr(r, attr) for r in vals if getattr(r, attr) is not None]
        return sum(nums) / len(nums) if nums else None

    return {
        "per": _avg("per"),
        "pbr": _avg("pbr"),
        "roe": _avg("roe"),
        "count": len(vals),
    }


def company_segments_hhi(db: Session, code: str) -> dict[str, float | None]:
    """부문 매출 기반 매출 집중도 HHI.

    HHI = Σ(점유율% ²)  (단위: pct^2). 커지면 집중도 ↑ (반독점 규제 대상).
    - HHI < 1500: 경쟁 분산
    - 1500 ≤ HHI < 2500: 보통 집중
    - HHI ≥ 2500: 고도로 집중
    ratio_pct 있는 항목만 사용. 데이터 부족 시 None.
    """
    from app.db.models import SegmentSales

    rows = db.scalars(
        select(SegmentSales)
        .where(SegmentSales.stock_code == code, SegmentSales.ratio_pct.is_not(None))
        .order_by(SegmentSales.bsns_year.desc(), SegmentSales.report_code.desc())
    ).all()
    if not rows:
        return {"hhi": None, "level": None, "count": 0}

    # 최신 year의 ratio_pct 사용
    latest_year = rows[0].bsns_year
    segments = [(r.segment_name, r.ratio_pct) for r in rows if r.bsns_year == latest_year and r.ratio_pct is not None]
    if not segments:
        return {"hhi": None, "level": None, "count": 0}

    hhi = sum(pct * pct for _, pct in segments)
    if hhi < 1500:
        level = "경쟁 분산"
    elif hhi < 2500:
        level = "보통 집중"
    else:
        level = "고도로 집중"
    return {"hhi": round(hhi, 1), "level": level, "count": len(segments)}


def company_material_prices(
    db: Session, code: str, settings: Settings | None = None
) -> list[dict[str, object]]:
    """uses_material 엣지의 원재료 노드별 가격 참조를 구한다.

    원재료 노드에 commodity_type(Gold, Copper 등 commodity 이름)이 있으면
    한국거래소/서울물이 대표 가격을 시도한다. 실패 시 None.
    """
    materials = company_materials(db, code)
    if not materials:
        return []

    out: list[dict[str, object]] = []
    for m in materials:
        item: dict[str, object] = {
            "node_id": m.get("node_id"),
            "korean_name": m.get("korean_name"),
            "edge_type": m.get("edge_type"),
            "share": m.get("share"),
            "price": None,
            "price_source": None,
        }
        # commodity_type 추출 시도
        node_id = m.get("node_id", "")
        static = _port().node(node_id) if node_id.startswith("MAT_") else None
        commodity_type = static.commodity_type if static and static.node_type == "raw_material" else None
        if not commodity_type:
            out.append(item)
            continue

        price = _fetch_commodity_price(commodity_type, settings)
        item["price"] = price
        item["price_source"] = commodity_type
        out.append(item)

    return out


def _fetch_commodity_price(commodity_type: str, settings: Settings | None) -> float | None:
    """원재료별 대표 현물 가격. 실패 시 None(강화 강등)."""
    # 한국거래소 API 또는 웹 검색 폴백
    try:
        from app.adapters.external import commodity_price
        p = commodity_price.get_price(commodity_type)
        if p:
            return p
    except Exception:
        pass
    # 웹 검색 폴백
    if settings:
        return _search_commodity_price(commodity_type, settings)
    return None


def _search_commodity_price(commodity_type: str, settings: Settings) -> float | None:
    """웹 검색으로 원재료 현물 가격 시도. 성공 시 float, 실패 시 None."""
    try:
        import requests as _requests

        from app.adapters.external import naver_search

        session = _requests.Session()
        query = f"{commodity_type} 현물 가격 원/kg"
        result = naver_search.search(query, session, sort="date", crawl_bodies=2)
        for hit in (result.get("bodies") or [])[:2]:
            text = hit.get("body", "")
            # "14,200원/kg" 같은 패턴 추출
            import re as _re
            m = _re.search(r"[\d,]+(?:\.\d+)?\s*원\s*/\s*kg", text)
            if m:
                num = float(m.group().replace(",", "").split("원")[0])
                return num
        for snippet in result.get("hits", [])[:3]:
            text = snippet.get("snippet", "")
            m = _re.search(r"[\d,]+(?:\.\d+)?\s*원\s*/\s*kg", text)
            if m:
                num = float(m.group().replace(",", "").split("원")[0])
                return num
    except Exception:
        pass
    return None


def product_peer_companies(db: Session, code: str) -> list[dict[str, object]]:
    """동일 제품군 노드 shared-product 관계인他の 종목 + 대표 재무비율.

    이 회사의 manufactures 엣지(제품) 중 canonical_id 가 다른 회사의 manufactures 와 겹치는
    종목을 찾아, 해당 종목의 시장 점유율· PER/PBR 을 조회한다.
    """
    from app.db.models import BusinessOntologyEdge, BusinessOntologyNode

    # 이 회사의 제품 노드 canonical_id 목록
    product_edges = db.scalars(
        select(BusinessOntologyEdge).where(
            BusinessOntologyEdge.stock_code == code,
            BusinessOntologyEdge.edge_type == "manufactures",
        )
    ).all()
    if not product_edges:
        return []

    product_ids = list({e.dst_node_id for e in product_edges})
    if not product_ids:
        return []

    # 다른 회사의 같은 제품 노드
    peer_edges = db.scalars(
        select(BusinessOntologyEdge).where(
            BusinessOntologyEdge.dst_node_id.in_(product_ids),
            BusinessOntologyEdge.edge_type == "manufactures",
            BusinessOntologyEdge.stock_code != code,
        )
    ).all()
    if not peer_edges:
        return []

    # 같은 제품인 peer stock_code 수집
    peer_codes = list({e.stock_code for e in peer_edges})
    peer_names = {
        c.stock_code: c.korean_name
        for c in db.scalars(
            select(BusinessOntologyNode).where(
                BusinessOntologyNode.stock_code.in_(peer_codes),
                BusinessOntologyNode.node_type == "company",
            )
        ).all()
    }

    # peer 재무비율 — 최신 추정 아닌 것
    from app.db.models import Financial

    rows = db.scalars(
        select(Financial)
        .where(Financial.stock_code.in_(peer_codes), Financial.is_estimate.is_(False))
        .order_by(Financial.period.desc())
    ).all()
    latest: dict[str, Financial] = {}
    for r in rows:
        if r.stock_code not in latest:
            latest[r.stock_code] = r

    out: list[dict[str, object]] = []
    for e in peer_edges:
        sc = e.stock_code
        if sc in out:
            continue
        fin = latest.get(sc)
        out.append({
            "stock_code": sc,
            "stock_name": peer_names.get(sc, ""),
            "per": fin.per if fin else None,
            "pbr": fin.pbr if fin else None,
            "market_cap": fin.market_cap if fin else None,
            "share": e.share,
        })
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


# ── pending_review 승격 워크플로(HITL) ──────────────────────────────────────
# 정규화 실패(canonical_id=NULL, confidence=0) 노드를 사람이 검수해 canonical 로 승격.
# 자동병합 금지 원칙 — 서비스는 후보만 제안하고 승격 결정은 라우터(HITL) 경유.

_CANON_PREFIX: dict[str, str] = {
    "company": "CMP_KRX_",
    "industry": "IND_GICS_",
    "product": "PRD_",
    "raw_material": "MAT_",
    "segment": "SEG_",
}


def _similarity(a: str, b: str) -> float:
    """문자열 유사도(0~1) — 후보 랭킹용. SequenceMatcher ratio."""
    return SequenceMatcher(None, a, b).ratio()


def _candidates_for(db: Session, node: BusinessOntologyNode, limit: int = 5) -> list[dict[str, object]]:
    """pending 노드에 대한 승격 후보 — 동일 type 기존 canonical 노드 + (회사면) CorpCodeMap fuzzy.

    merge 용 후보만 제공. 새 canonical 발급은 리뷰어가 직접 ID 를 입력.
    """
    name = node.korean_name
    cands: list[dict[str, object]] = []

    if node.node_type == "company":
        rows = db.execute(select(CorpCodeMap.stock_code, CorpCodeMap.corp_name)).all()
        for r in rows:
            score = _similarity(name, r.corp_name)
            if score > 0:
                cands.append(
                    {
                        "canonical_id": f"CMP_KRX_{r.stock_code}",
                        "korean_name": r.corp_name,
                        "node_type": "company",
                        "score": round(score, 3),
                        "stock_code": r.stock_code,
                    }
                )

    # 기존 canonical 노드 동일 type — canonical_id 별 1건 dedup.
    existing = db.scalars(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.node_type == node.node_type,
            BusinessOntologyNode.status == "canonical",
            BusinessOntologyNode.canonical_id.is_not(None),
        )
    ).all()
    by_cid: dict[str, BusinessOntologyNode] = {}
    for n in existing:
        by_cid.setdefault(n.canonical_id, n)
    for n in by_cid.values():
        score = _similarity(name, n.korean_name)
        if score > 0:
            cands.append(
                {
                    "canonical_id": n.canonical_id,
                    "korean_name": n.korean_name,
                    "node_type": n.node_type,
                    "score": round(score, 3),
                    "stock_code": n.stock_code if n.node_type == "company" else None,
                }
            )

    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for c in sorted(cands, key=lambda x: x["score"], reverse=True):
        cid = str(c["canonical_id"])
        if cid in seen:
            continue
        seen.add(cid)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def list_pending(
    db: Session,
    *,
    node_type: str | None = None,
    stock_code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """pending_review 노드 목록 + 승격 후보. status='pending_review'(canonical_id NULL) 만."""
    conds = [BusinessOntologyNode.status == "pending_review"]
    if node_type:
        conds.append(BusinessOntologyNode.node_type == node_type)
    if stock_code:
        conds.append(BusinessOntologyNode.stock_code == stock_code)

    total = db.scalar(select(func.count(BusinessOntologyNode.id)).where(*conds)) or 0
    rows = db.scalars(
        select(BusinessOntologyNode)
        .where(*conds)
        .order_by(BusinessOntologyNode.id)
        .limit(limit)
        .offset(offset)
    ).all()
    pending = [
        {
            "id": n.id,
            "node_type": n.node_type,
            "korean_name": n.korean_name,
            "english_name": n.english_name,
            "stock_code": n.stock_code,
            "confidence": n.confidence,
            "candidates": _candidates_for(db, n),
        }
        for n in rows
    ]
    return {"pending": pending, "total": total}


def promote_pending(
    db: Session, node_id: int, canonical_id: str, action: str
) -> dict[str, object] | None:
    """pending 노드 → canonical 승격(promote-in-place).

    merge: canonical_id 가 기존 canonical 노드에 존재해야 함(해당 정준으로 합류).
    new: canonical_id 가 미존재해야 함(신규 정준 발급). 둘 다 노드 타입 접두어 검증.
    승격 시 해당 행의 canonical_id·status 만 갱신 — 엣지는 node PK 기준이라 재포인팅 불필요,
    explore 가 canonical_id 로 PK 집합을 모아 dedup 하므로 중복 행도 read 시 통합.
    """
    n = db.get(BusinessOntologyNode, node_id)
    if n is None:
        return None
    if n.status != "pending_review":
        raise ValueError(f"node {node_id} is not pending_review (status={n.status})")

    prefix = _CANON_PREFIX.get(n.node_type)
    if not prefix or not canonical_id.startswith(prefix):
        raise ValueError(f"canonical_id must start with {prefix!r} for {n.node_type}")

    exists = db.scalar(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.canonical_id == canonical_id,
            BusinessOntologyNode.status == "canonical",
        )
    )
    if action == "merge":
        if exists is None:
            raise ValueError(f"merge target canonical_id not found: {canonical_id}")
    elif action == "new":
        if exists is not None:
            raise ValueError(f"canonical_id already exists: {canonical_id} (use merge)")
    else:
        raise ValueError("action must be 'merge' or 'new'")

    n.canonical_id = canonical_id
    n.status = "canonical"
    n.confidence = 1.0  # 사람이 검수한 정준 매칑 — 최고 신뢰도
    db.commit()
    db.refresh(n)
    return {
        "id": n.id,
        "node_type": n.node_type,
        "korean_name": n.korean_name,
        "canonical_id": n.canonical_id,
        "status": n.status,
        "stock_code": n.stock_code,
    }


def reject_pending(db: Session, node_id: int) -> dict[str, object] | None:
    """pending 노드 거부 — status='rejected'(explore·pending 목록에서 모두 제외). 노드·엣지는 보존."""
    n = db.get(BusinessOntologyNode, node_id)
    if n is None:
        return None
    if n.status != "pending_review":
        raise ValueError(f"node {node_id} is not pending_review (status={n.status})")
    n.status = "rejected"
    db.commit()
    db.refresh(n)
    return {"id": n.id, "node_type": n.node_type, "korean_name": n.korean_name, "status": n.status, "stock_code": n.stock_code}


# ── industry 자유표현 자동 매핑(임베딩 top-k + LLM 판정 폴백) ─────────────────
# 정규화(키워드+퍼지)가 잡지 못한 industry 자유표현을 GICS 128 sub-industry 로 의미 매칭.
# cloud LLM 은 /api/embeddings 미지원 → 임베딩은 로컬 Ollama(qwen3-embedding), 판정은 cloud LLM(chat).
_GICS_EMBED_CACHE: dict[str, tuple[list[str], list[list[float]]]] = {}
_GICS_EMBED_LOCK = threading.Lock()

_INDUSTRY_CLASSIFY_SYSTEM = (
    "너는 GICS 산업 분류 전문가다. 주어진 자유표현 산업명과 후보 GICS sub-industry 목록(번호:한국명(영문명))을 보고 "
    "가장 잘 어울리는 하나의 후보 번호만 고르거나, 어느 것도 잘 안 맞으면 NONE 이라고 답해라. "
    "회사명·고객군·지역 등 산업 분류가 아닌 표현이면 NONE. 답은 오직 후보 번호 또는 NONE 만 출력한다."
)

# 분류 chat 은 짧은 프롬프트/응답 — 딥다이브용 300s timeout 이 병리 hang(trickle)을 15min 까지 늘리는 것을 방지.
# timeout 은 _stream_message 의 **전체 deadline**: 정상 응답(12~80s)은 허용하되, trickle hang(바이트가
# 느리게 흘러 read timeout 에 안 걸리는 경우)을 90s 에서 절단. 재시도 1회 — hang 재시도는 무의미(강등=pending 안전).
_INDUSTRY_CLASSIFY_TIMEOUT_S = 90
_INDUSTRY_CLASSIFY_MAX_ATTEMPTS = 1

# 배치 판정 — N개 표현을 1개 chat 호출로 묶어 판정. cloud LLM 이 동시 요청을 직렬화하므로 per-call 병렬화는
# 무효(N×60s 순차 합산). 배치는 1회 호출로 N건 판정 → N 이 늘어도 ~1회 분량(~60~120s) 유지.
# 프롬프트·응답이 N 에 비례해 커지므로 deadline 은 배치용으로 넉넉히.
_INDUSTRY_BATCH_TIMEOUT_S = 180
# 배치 청크 크기 — 한 chat 호출에 담을 표현 수 상한. N 이 클 때(수백 건) 한 덩어리로 보내면 프롬프트가
# 거대해져 LLM 이 trickle/hang 하고 deadline 도 효과 없다. 청크로 쪼개 순차 호출(청크당 ~수초~수십초).
_INDUSTRY_BATCH_CHUNK = 20


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _gics_embeddings(llm: LLMPort, embed_model: str) -> tuple[list[str], list[list[float]]] | None:
    """GICS 128 sub-industry 임베딩(프로세스 수명 캐시). korean+english+aliases 합성 텍스트. 실패 시 None.

    캐시 키는 embed_model(인스턴스 id 아님) — reprocess 호출마다 새 LLMPort 인스턴스가 와도 동일 모델이면
    캐시 재사용(53s 빌드 최초 1회). 병렬 resolve_industry 동시 빌드 방지를 위해 double-checked lock.
    """
    if embed_model in _GICS_EMBED_CACHE:
        return _GICS_EMBED_CACHE[embed_model]
    with _GICS_EMBED_LOCK:
        if embed_model in _GICS_EMBED_CACHE:  # double-check: 다른 스레드가 이미 빌드했을 수 있음
            return _GICS_EMBED_CACHE[embed_model]
        inds = _port().list_industries()
        ids: list[str] = []
        texts: list[str] = []
        for ind in inds:
            ids.append(ind.id)
            parts = [ind.korean_name, ind.english_name, *ind.aliases]
            texts.append(" / ".join(p for p in parts if p))
        try:
            vecs = llm.embed(embed_model, texts)
        except LLMError:
            return None
        cached = (ids, vecs)
        _GICS_EMBED_CACHE[embed_model] = cached
        return cached


def resolve_industry(
    db: Session,
    raw: str,
    *,
    llm: LLMPort | None = None,
    embed_model: str = "",
    judge_model: str = "",
) -> BusinessNormalizeResult:
    """industry 자유표현 → 정준 ID. 키워드+퍼지(포트) → pending 시 임베딩 top-k + LLM 판정 폴백.

    LLM/임베딩 미설정·실패 시 포트의 pending 결과 그대로 반환(우아한 강등). LLM 판정은 보수적 —
    회사군·고객군·지역 등 산업 분류가 아닌 표현은 NONE → pending 유지(HITL 대상).
    """
    r = _port().resolve(raw, "industry")
    if r.resolved:
        return r
    if llm is None or not embed_model or not judge_model:
        return r  # 폴백 불가 → pending 유지
    cache = _gics_embeddings(llm, embed_model)
    if cache is None:
        return r
    ids, vecs = cache
    try:
        qvec = llm.embed(embed_model, [raw])[0]
    except LLMError:
        return r
    scored = sorted(zip(ids, vecs, strict=False), key=lambda iv: _cosine(qvec, iv[1]), reverse=True)
    inds = {ind.id: ind for ind in _port().list_industries()}
    cand_lines: list[str] = []
    cand_ids: list[str] = []
    for i, (gid, _) in enumerate(scored[:5], 1):
        ind = inds.get(gid)
        if ind is None:
            continue
        cand_lines.append(f"{i}:{ind.korean_name}({ind.english_name})")
        cand_ids.append(gid)
    if not cand_ids:
        return r
    user = f"자유표현: {raw}\n후보:\n" + "\n".join(cand_lines)
    try:
        out = llm.chat(
            judge_model,
            _INDUSTRY_CLASSIFY_SYSTEM,
            user,
            temperature=0.0,
            timeout=_INDUSTRY_CLASSIFY_TIMEOUT_S,
            max_attempts=_INDUSTRY_CLASSIFY_MAX_ATTEMPTS,
        ).strip()
    except LLMError:
        return r
    pick = out.split()[0].strip().upper() if out else "NONE"
    if pick == "NONE" or not pick.isdigit() or not (1 <= int(pick) <= len(cand_ids)):
        return r  # NONE 또는 파싱 실패 → pending 유지
    gid = cand_ids[int(pick) - 1]
    return BusinessNormalizeResult(
        term=raw,
        node_type="industry",
        canonical_id=gid,
        matched_via="llm_classify",
        status="canonical",
        confidence=0.7,
    )


# 배치 판정 응답 파서 — "<표현번호>: <후보번호|NONE>" 한 줄씩. 번호 밖의 텍스트·빈 줄 무시.
_BATCH_LINE_RE = re.compile(r"^\s*(\d+)\s*[:.、]\s*([0-9A-Za-z]+)")


def _pending_industry(raw: str) -> BusinessNormalizeResult:
    return BusinessNormalizeResult(
        term=raw, node_type="industry", canonical_id=None,
        matched_via="", status="pending_review", confidence=0.0,
    )


def _classify_industry_batch(
    llm: LLMPort,
    embed_model: str,
    judge_model: str,
    items: list[tuple[int, str]],
) -> dict[int, BusinessNormalizeResult]:
    """포트 무매치 industry 표현들을 청크 단위 chat 호출로 일괄 판정 → {node_id: result}.

    cloud LLM 이 동시 요청을 직렬화하므로 per-call 병렬화는 무효 — N건을 한 프롬프트에 나열해 판정.
    단 한 덩어리로 보내면 N 이 클 때(수백 건) 프롬프트가 거대해져 LLM 이 trickle/hang 하고 deadline 도
    효과가 없다 → _INDUSTRY_BATCH_CHUNK 청크로 쪼개 순차 호출(청크당 로컬 번호 1..K). 쿼리 임베딩은
    1회 /api/embed 배치, top-5 후보도 1회 계산해 청크 호출에 재사용. 실패·미응답·NONE·파싱실패 → pending 강등.
    """
    if not items:
        return {}
    cache = _gics_embeddings(llm, embed_model)
    if cache is None:
        return {nid: _pending_industry(raw) for nid, raw in items}
    ids, vecs = cache
    inds = {ind.id: ind for ind in _port().list_industries()}
    # 쿼리 임베딩 1회 배치 — N번 round-trip 대신 한 번.
    try:
        qvecs = llm.embed(embed_model, [raw for _, raw in items])
    except LLMError:
        return {nid: _pending_industry(raw) for nid, raw in items}
    if len(qvecs) != len(items):
        return {nid: _pending_industry(raw) for nid, raw in items}

    # 표현별 top-5 후보 + 표시 텍스트를 미리 계산(임베딩/스코어링은 1회, 청크 호출에 재사용).
    cand_ids_per: list[list[str]] = []
    cand_text_per: list[str] = []
    for (_nid, raw), qvec in zip(items, qvecs, strict=False):
        scored = sorted(zip(ids, vecs, strict=False), key=lambda iv: _cosine(qvec, iv[1]), reverse=True)
        cids = [gid for gid, _ in scored[:5] if gid in inds]
        cand_ids_per.append(cids)
        if cids:
            cands = "\n".join(
                f"{i}:{inds[gid].korean_name}({inds[gid].english_name})" for i, gid in enumerate(cids, 1)
            )
        else:
            cands = "(후보 없음)"
        cand_text_per.append(f"{raw}\n후보:\n{cands}")

    results: dict[int, BusinessNormalizeResult] = {}
    for start in range(0, len(items), _INDUSTRY_BATCH_CHUNK):
        end = min(start + _INDUSTRY_BATCH_CHUNK, len(items))
        results.update(
            _classify_industry_chunk(
                llm,
                judge_model,
                items[start:end],
                cand_text_per[start:end],
                cand_ids_per[start:end],
            )
        )
    return results


def _classify_industry_chunk(
    llm: LLMPort,
    judge_model: str,
    chunk_items: list[tuple[int, str]],
    chunk_texts: list[str],
    chunk_cids: list[list[str]],
) -> dict[int, BusinessNormalizeResult]:
    """한 청크(≤ _INDUSTRY_BATCH_CHUNK 표현)를 1회 chat 으로 판정. [표현 N] 은 청크 내 로컬 1..K."""
    # 청크 내 로컬 번호 부여 — 파서 idx 도 로컬 1..K 를 가리킨다.
    blocks = [f"[표현 {i + 1}] {t}" for i, t in enumerate(chunk_texts)]
    user = (
        "아래 각 자유표현 산업명에 대해, 제시된 후보 중 가장 잘 어울리는 하나의 후보 번호를 고르거나, "
        "어느 것도 아니면 NONE 이라 답해라. 회사명·고객군·지역·공공 등 산업 분류가 아닌 표현은 NONE. "
        "출력: 각 표현마다 한 줄, '<표현번호>: <후보번호|NONE>' 만 출력.\n\n"
        + "\n\n".join(blocks)
    )
    try:
        out = llm.chat(
            judge_model,
            _INDUSTRY_CLASSIFY_SYSTEM,
            user,
            temperature=0.0,
            timeout=_INDUSTRY_BATCH_TIMEOUT_S,
            max_attempts=_INDUSTRY_CLASSIFY_MAX_ATTEMPTS,
        ).strip()
    except LLMError:
        return {nid: _pending_industry(raw) for nid, raw in chunk_items}

    results: dict[int, BusinessNormalizeResult] = {}
    for line in out.splitlines():
        m = _BATCH_LINE_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        pick = m.group(2).upper()
        if not (1 <= idx <= len(chunk_items)):
            continue
        nid, raw = chunk_items[idx - 1]
        cids = chunk_cids[idx - 1]
        if pick == "NONE" or not pick.isdigit() or not (1 <= int(pick) <= len(cids)):
            results[nid] = _pending_industry(raw)
        else:
            results[nid] = BusinessNormalizeResult(
                term=raw,
                node_type="industry",
                canonical_id=cids[int(pick) - 1],
                matched_via="llm_classify",
                status="canonical",
                confidence=0.7,
            )
    # 미응답 표현은 pending 강등(LLM 이 줄을 빼먹거나 포맷 이탈 시).
    for nid, raw in chunk_items:
        if nid not in results:
            results[nid] = _pending_industry(raw)
    return results


def reprocess_pending(
    db: Session, *, node_type: str | None = None, settings: Settings | None = None
) -> dict[str, object]:
    """pending_review 노드를 개선된 normalizer로 재해석해 일괄 승격/거부.

    LLM NER 재실행 없이 (korean_name, node_type) 만 normalizer에 재투입 — 라이브 DART/Ollama 소비 없음.
    - canonical(auto_new/keyword/사전 매칭): canonical_id·status·confidence in-place 갱신
    - rejected: status='rejected'
    - 여전히 pending_review: 유지
    회사는 resolve_company(시드 + CorpCodeMap exact + 자동 new), industry는 resolve_industry(키워드+퍼지,
    settings 주어지면 임베딩 top-k + LLM 배치 판정 폴백), 비회사는 normalize_one(포트).

    industry 판정은 _classify_industry_batch — N개 표현을 1회 chat 호출로 묶어 판정(cloud LLM 이 동시
    요청을 직렬화하므로 per-call 병렬화는 무효). 포트 키워드/퍼지로 즉시 해결되는 것은 배치에서 제외해
    프롬프트를 줄인다. DB 갱신은 메인 스레드에서 순차.
    """
    llm: LLMPort | None = None
    embed_model = ""
    judge_model = ""
    if settings is not None:
        llm = _get_llm(settings)
        embed_model = settings.ollama_embedding_model
        judge_model = settings.insight_model

    conds = [BusinessOntologyNode.status == "pending_review"]
    if node_type:
        conds.append(BusinessOntologyNode.node_type == node_type)
    rows = db.scalars(
        select(BusinessOntologyNode).where(*conds).order_by(BusinessOntologyNode.id)
    ).all()

    # industry 판정 — 포트(keyword/fuzzy) 즉시 해결은 배치에서 제외, 나머지를 1회 chat 으로 일괄 판정.
    industry_nodes = [n for n in rows if n.node_type == "industry"]
    ind_results: dict[int, BusinessNormalizeResult] = {}
    if industry_nodes:
        pending_for_llm: list[tuple[int, str]] = []
        for n in industry_nodes:
            r = _port().resolve(n.korean_name, "industry")
            if r.resolved:
                ind_results[n.id] = r
            elif r.status == "rejected":
                # 비산업(공공/서비스/제조)은 포트가 reject 판정 — LLM 폴백 없이 거부.
                ind_results[n.id] = r
            elif llm is not None and embed_model and judge_model:
                pending_for_llm.append((n.id, n.korean_name))
            else:
                ind_results[n.id] = r  # 폴백 불가 → pending 유지
        if pending_for_llm:
            ind_results.update(
                _classify_industry_batch(llm, embed_model, judge_model, pending_for_llm)
            )

    promoted = 0
    rejected = 0
    still_pending = 0
    for n in rows:
        if n.node_type == "company":
            r = resolve_company(db, n.korean_name)
        elif n.node_type == "industry":
            # ind_results 가 모든 industry 노드를 채운다(포트/배치/폴백). 누락 시 pending 안전 기본.
            r = ind_results.get(n.id, _pending_industry(n.korean_name))
        else:
            r = normalize_one(n.korean_name, n.node_type)
        if r.status == "canonical" and r.canonical_id:
            n.canonical_id = r.canonical_id
            n.status = "canonical"
            n.confidence = r.confidence
            promoted += 1
        elif r.status == "rejected":
            n.status = "rejected"
            rejected += 1
        else:
            still_pending += 1
    db.commit()
    return {
        "promoted": promoted,
        "rejected": rejected,
        "still_pending": still_pending,
        "total": len(rows),
    }


# --- 비동기 reprocess ---
# 대규모 pending(수백 건)은 cloud LLM 분류가 ~1.5s/건이라 수십 분 걸려 동기 HTTP 를 블로킹한다.
# 백그라운드 스레드로 실행해 엔드포인트는 즉시 202 반환, 클라이언트는 status 엔드포인트로 폴링.
# 락으로 중복 실행 방지(동시 reprocess 는 DB 경합·이중 승격 위험).
_REPROCESS_LOCK = threading.Lock()
_REPROCESS_STATE: dict[str, object] = {"running": False, "last": None, "error": None}


def start_reprocess_background(
    *, node_type: str | None = None, settings: Settings | None = None
) -> dict[str, str]:
    """reprocess 를 백그라운드 스레드로 시작. 이미 실행 중이면 "already_running".

    락 비획득 시 즉시 반환(중복 실행 방지). 획득 시 데몬 스레드로 _run_reprocess 실행.
    """
    if not _REPROCESS_LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    _REPROCESS_STATE["running"] = True
    _REPROCESS_STATE["error"] = None

    def _run() -> None:
        try:
            from app.db.session import SessionLocal

            db = SessionLocal()
            try:
                _REPROCESS_STATE["last"] = reprocess_pending(db, node_type=node_type, settings=settings)
            finally:
                db.close()
        except Exception:  # 백그라운드 예외는 스레드 죽음 방지용으로 흡수, 상태에 기록
            logger.exception("reprocess 백그라운드 실행 실패")
            _REPROCESS_STATE["error"] = "reprocess 백그라운드 실행 실패(로그 확인)"
        finally:
            _REPROCESS_STATE["running"] = False
            _REPROCESS_LOCK.release()

    threading.Thread(target=_run, daemon=True, name="reprocess-pending").start()
    return {"status": "started"}


def reprocess_status() -> dict[str, object]:
    """백그라운드 reprocess 상태 — running 여부 + 직전 완료 결과(last) + error."""
    return {
        "running": _REPROCESS_STATE["running"],
        "last": _REPROCESS_STATE["last"],
        "error": _REPROCESS_STATE["error"],
    }
