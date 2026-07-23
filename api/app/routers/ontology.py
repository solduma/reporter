"""재무 온톨로지 라우터 — 정규화·비율 계산·메타데이터 조회 엔드포인트.

온톨로지는 정적 데이터라 DB 의존 없이 services.ontology 경유로 포트(어댑터) 호출.
기존 재무 흐름(/financial-statements)과 독립 — 2차-B 첫 스프린트(엔드포인트 노출만).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query

from app import schemas
from app.ports.financial_ontology import RatioResultOut
from app.services import ontology as ontology_service

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


@router.post("/normalize", response_model=schemas.OntologyNormalizeResponse)
def normalize(req: schemas.OntologyNormalizeRequest) -> schemas.OntologyNormalizeResponse:
    """계정명·DART taxonomy 요소를 온톨로지 ID 로 일괄 정규화. 공시 항목→온톨로지 매핑 품질 점검용."""
    results = ontology_service.normalize(req.terms, standard=req.standard)
    items = [
        schemas.OntologyNormalizeItem(term=r.term, id=r.id, matched_via=r.matched_via, resolved=r.resolved)
        for r in results
    ]
    coverage = sum(1 for i in items if i.resolved) / len(items) if items else 1.0
    return schemas.OntologyNormalizeResponse(items=items, coverage=coverage)


@router.post("/ratio", response_model=schemas.OntologyRatioResult)
def calculate_ratio(req: schemas.OntologyRatioValueRequest) -> schemas.OntologyRatioResult:
    """단일 비율 평가. values: 계정 ID(평균/closing) + 명시 기간(id:opening) + 외부 입력(shares_outstanding)."""
    r = ontology_service.calculate_one(req.ratio_id, req.values)
    return _ratio_out(r)


@router.post("/ratios", response_model=list[schemas.OntologyRatioResult])
def calculate_ratios(req: schemas.OntologyRatiosRequest) -> list[schemas.OntologyRatioResult]:
    """다수 비율 일괄 평가. 동일 values 로 여러 비율을 한 번에 계산."""
    return [_ratio_out(r) for r in ontology_service.calculate_ratios(req.ratio_ids, req.values)]


@router.get("/ratios", response_model=list[schemas.OntologyRatioMeta])
def list_ratios(category: str | None = Query(default=None)) -> list[schemas.OntologyRatioMeta]:
    """비율 정의 목록(카테고리 필터: profitability|liquidity|leverage|valuation)."""
    return [
        schemas.OntologyRatioMeta(**asdict(r)) for r in ontology_service.ratios(category=category)
    ]


@router.get("/accounts", response_model=list[schemas.OntologyAccountMeta])
def list_accounts(
    statement: str | None = Query(
        default=None,
        description="balance_sheet|income_statement|comprehensive_income|changes_in_equity|cash_flow",
    ),
) -> list[schemas.OntologyAccountMeta]:
    """계정 메타데이터 목록(명세서 필터)."""
    return [
        schemas.OntologyAccountMeta(**asdict(a))
        for a in ontology_service.accounts(statement=statement)
    ]


@router.get("/accounts/{account_id}", response_model=schemas.OntologyAccountMeta)
def get_account(account_id: str) -> schemas.OntologyAccountMeta:
    """단일 계정 메타데이터. 없으면 404."""
    a = ontology_service.account(account_id)
    if a is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"unknown account: {account_id}")
    return schemas.OntologyAccountMeta(**asdict(a))


@router.post("/metric-info", response_model=schemas.OntologyMetricInfoResponse)
def metric_info(req: schemas.OntologyMetricInfoRequest) -> schemas.OntologyMetricInfoResponse:
    """Financial 컬럼 key 들의 온톨로지 정준 라벨·설명 조회(B1 라벨 단일 출처)."""
    items, coverage = ontology_service.metric_info(req.keys)
    return schemas.OntologyMetricInfoResponse(
        items=[schemas.OntologyMetricInfoItem(**it) for it in items],
        coverage=coverage,
    )


def _ratio_out(r: RatioResultOut) -> schemas.OntologyRatioResult:
    return schemas.OntologyRatioResult(
        ratio_id=r.ratio_id,
        value=str(r.value) if r.value is not None else None,
        ok=r.ok,
        missing=list(r.missing),
        warnings=list(r.warnings),
        reason=r.reason,
    )
