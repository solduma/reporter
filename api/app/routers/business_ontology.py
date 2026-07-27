"""비즈니스 온톨로지 라우터 — 정규화·정적 조회·회사 그래프 엔드포인트.

정적 온톨로지(정규화·GICS 트리·노드/엣지타입)는 services.business_ontology 경유로 포트 호출.
회사 그래프·부문 매출은 DB 영속 데이터(엣지 인스턴스)를 서비스가 읽는다.
v1 스켈레톤 — DB 백엔드 엔드포인트는 테이블이 비어있어 빈 결과 반환(Task #28 에서 채움).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import schemas
from app.db.session import get_session
from app.services import business_ontology as bo_service

router = APIRouter(prefix="/api/business-ontology", tags=["business-ontology"])


@router.post("/normalize", response_model=schemas.BusinessNormalizeResponse)
def normalize(req: schemas.BusinessNormalizeRequest) -> schemas.BusinessNormalizeResponse:
    """raw mention (name, node_type) 일괄 정규화 → 정준 노드 ID. NER→온톨로지 매핑 품질 점검용."""
    results = bo_service.normalize(req.mentions, standard=req.standard)
    items = [_normalize_item(r) for r in results]
    coverage = sum(1 for i in items if i.resolved) / len(items) if items else 1.0
    return schemas.BusinessNormalizeResponse(items=items, coverage=coverage)


@router.get("/industries", response_model=list[schemas.BusinessIndustryNodeOut])
def list_industries() -> list[schemas.BusinessIndustryNodeOut]:
    """GICS 산업 풀(11/24/54/128 4단계 rollup) 평면 목록."""
    return [schemas.BusinessIndustryNodeOut(**asdict(n)) for n in bo_service.industries()]


@router.get("/industries/{gics_code}", response_model=schemas.BusinessIndustryNodeOut)
def get_industry(gics_code: str) -> schemas.BusinessIndustryNodeOut:
    """GICS 코드(8자리) 또는 노드 ID 로 단일 산업 조회. 없으면 404."""
    n = bo_service.industry(gics_code)
    if n is None:
        raise HTTPException(status_code=404, detail=f"unknown industry: {gics_code}")
    return schemas.BusinessIndustryNodeOut(**asdict(n))


@router.get("/edge-types", response_model=list[schemas.BusinessEdgeTypeOut])
def list_edge_types() -> list[schemas.BusinessEdgeTypeOut]:
    """엣지 타입 메타 목록(15종)."""
    return [schemas.BusinessEdgeTypeOut(**asdict(e)) for e in bo_service.edge_types()]


@router.get("/nodes", response_model=list[schemas.BusinessNodeOut])
def list_nodes(
    node_type: str | None = Query(
        default=None,
        description="company|industry|product|raw_material|segment. 미지정 시 전체.",
    ),
) -> list[schemas.BusinessNodeOut]:
    """정적 노드 목록(시드 products/materials/companies/segments/industries)."""
    return [schemas.BusinessNodeOut(**asdict(n)) for n in bo_service.nodes(node_type=node_type)]  # type: ignore[arg-type]


@router.get("/nodes/{node_id}", response_model=schemas.BusinessNodeOut)
def get_node(node_id: str) -> schemas.BusinessNodeOut:
    """단일 정적 노드 조회(ID). 없으면 404."""
    n = bo_service.node(node_id)
    if n is None:
        raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
    return schemas.BusinessNodeOut(**asdict(n))


# ── 회사 그래프·부문 매출(DB 영속 — Task #28 에서 실데이터) ────────────────
@router.get("/explore", response_model=schemas.BusinessExploreOut)
def explore_node(
    node_id: str = Query(..., description="canonical_id (예: CMP_KRX_005930, PRD_SEMI_DRAM, IND_GICS_45102010)"),
    db: Session = Depends(get_session),
) -> schemas.BusinessExploreOut:
    """노드 중심 1-hop 탐색 — focal 의 모든 stock_code 인스턴스를 모아 cross-stock 이웃 구성.

    회사/제품/원재료/산업 어느 노드든 focal. 기존 /{code}/graph(회사 스코프)와 달리
    계층을 가로지르는 탐색(기업→산업→동종업→그 기업의 제품/원재료)을 지원.
    """
    g = bo_service.explore_node(db, node_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
    return schemas.BusinessExploreOut(
        focal=schemas.BusinessExploreNodeOut(**g["focal"]),  # type: ignore[arg-type]
        neighbors=[schemas.BusinessExploreNeighborOut(**n) for n in g.get("neighbors", [])],
        edges=[schemas.BusinessEdgeOut(**e) for e in g.get("edges", [])],
    )


@router.get("/{code}/graph", response_model=schemas.BusinessGraphOut)
def company_graph(code: str, db: Session = Depends(get_session)) -> schemas.BusinessGraphOut:
    """회사 비즈니스 그래프 — 노드 + 엣지(operates_in/manufactures/uses_material/...)."""
    g = bo_service.company_graph(db, code)
    return schemas.BusinessGraphOut(
        nodes=[schemas.BusinessNodeOut(**n) for n in g.get("nodes", [])],
        edges=[schemas.BusinessEdgeOut(**e) for e in g.get("edges", [])],
    )


@router.get("/{code}/segments", response_model=list[schemas.BusinessSegmentOut])
def company_segments(
    code: str,
    year: str | None = Query(default=None, description="사업연도(예: 2024). 미지정시 전체."),
    db: Session = Depends(get_session),
) -> list[schemas.BusinessSegmentOut]:
    """부문별 매출(iotHom3MdQe). 산업/제품/지역/매출형태 부문."""
    return [
        schemas.BusinessSegmentOut(**s) for s in bo_service.company_segments(db, code, year=year)
    ]


@router.get("/{code}/products", response_model=list[schemas.BusinessCompanyEdgeOut])
def company_products(
    code: str, db: Session = Depends(get_session)
) -> list[schemas.BusinessCompanyEdgeOut]:
    """회사가 생산하는 제품 + 매출 비중(manufactures 엣지)."""
    return [schemas.BusinessCompanyEdgeOut(**p) for p in bo_service.company_products(db, code)]


@router.get("/{code}/materials", response_model=list[schemas.BusinessCompanyEdgeOut])
def company_materials(
    code: str, db: Session = Depends(get_session)
) -> list[schemas.BusinessCompanyEdgeOut]:
    """회사가 사용하는 원재료(uses_material 엣지)."""
    return [schemas.BusinessCompanyEdgeOut(**m) for m in bo_service.company_materials(db, code)]


@router.get("/industry/{gics_code}/companies", response_model=list[schemas.BusinessPeerOut])
def industry_companies(
    gics_code: str, db: Session = Depends(get_session)
) -> list[schemas.BusinessPeerOut]:
    """GICS 동종업 종목(peers) — 스크리너 동종업 비교용."""
    return [schemas.BusinessPeerOut(**c) for c in bo_service.industry_companies(db, gics_code)]


def _normalize_item(r) -> schemas.BusinessNormalizeItem:
    return schemas.BusinessNormalizeItem(
        term=r.term,
        node_type=r.node_type or "",
        canonical_id=r.canonical_id,
        matched_via=r.matched_via,
        status=r.status,
        confidence=r.confidence,
        resolved=r.resolved,
    )
