"""사업 개요 라우터 — 종목별 사업 개요 조회(cache-aside) + 단건 갱신 트리거.

데이터 접근·추출·조립은 services/business_ingest 가 담당. 라우터는 캐시를 먼저 보고(12h TTL),
miss 시 조립 job 을 큐잉하고 즉시 null 반환(조립은 worker 폴링 큐가 백그라운드 실행 — 수 분
소요를 요청 스레드에서 기다리면 웹이 타임아웃한다). POST /refresh 도 동일하게 비동기.
공시 → DB → Cache 흐름의 응답 edge.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import (
    AssemblyStatus,
    BusinessOverviewOut,
    ResearchGuidelineInput,
    ResearchStatus,
)
from app.services import business_assembly, business_ingest, business_research

router = APIRouter(prefix="/api/companies", tags=["business"])


def _to_out(payload: dict) -> BusinessOverviewOut:
    return BusinessOverviewOut.model_validate(payload)


@router.get("/{code}/business", response_model=BusinessOverviewOut | None)
def get_business_overview(
    code: str,
    db: Session = Depends(get_session),
    response: Response = Response(),
) -> BusinessOverviewOut | None:
    """종목 사업 개요 — 캐시 우선(12h TTL), miss 시 조립 job 큐잉 후 즉시 null.

    웹은 GET /business/assembly/status 를 폴링하다 done 이면 본 엔드포인트를 재호출한다.
    """
    cached = business_ingest.get_cached_overview(db, code)
    if cached is not None:
        response.headers["Cache-Control"] = "public, max-age=300"
        return _to_out(cached)
    business_assembly.enqueue(db, code)
    return None


@router.post("/{code}/business/refresh", response_model=BusinessOverviewOut | None)
def refresh_business_overview(
    code: str,
    db: Session = Depends(get_session),
) -> BusinessOverviewOut | None:
    """단건 사업 개요 재조립 요청(비동기). 캐시 무효화 + job 큐잉 후 즉시 null 반환.

    새 정기보고서 반영이 필요할 때 호출. 완료는 GET /business/assembly/status 로 확인.
    """
    business_ingest.invalidate_cache(db, code)
    business_assembly.enqueue(db, code)
    return None


@router.get("/{code}/business/assembly/status", response_model=AssemblyStatus)
def get_business_assembly_status(
    code: str,
    db: Session = Depends(get_session),
) -> AssemblyStatus:
    """사업 개요 조립 job 상태 폴링(진행률/완료/실패)."""
    job = business_assembly.latest_job(db, code)
    if job is None:
        return AssemblyStatus(stock_code=code, status="none", progress=0)
    return AssemblyStatus(
        stock_code=code,
        status=job.status,
        progress=job.progress,
        error=job.error,
    )


@router.post("/{code}/business/research", response_model=ResearchStatus)
def request_business_research(
    code: str,
    input: ResearchGuidelineInput,
    db: Session = Depends(get_session),
) -> ResearchStatus:
    """사업 리서치 요청(비동기 큐). 가이드라인을 받아 enqueue → 결과는 research_summary에 병합.

    이미 진행 중(pending|running) job이 있으면 그것을 반환(dedup). 완료 후 GET /business에서
    research_summary 확인 가능.
    """
    job = business_research.enqueue(db, code, input.guideline)
    # research_summary 존재 여부 확인.
    cached = db.scalar(
        select(business_ingest.BusinessOverviewCache).where(
            business_ingest.BusinessOverviewCache.stock_code == code
        )
    )
    has_summary = bool(cached and cached.payload and cached.payload.get("research_summary"))
    return ResearchStatus(
        stock_code=code,
        status=job.status,
        progress=job.progress,
        error=job.error,
        has_summary=has_summary,
    )


@router.get("/{code}/business/research/status", response_model=ResearchStatus)
def get_business_research_status(
    code: str,
    db: Session = Depends(get_session),
) -> ResearchStatus:
    """사업 리서치 상태 폴링(진행률/완료/실패)."""
    job = business_research.latest_job(db, code)
    if job is None:
        return ResearchStatus(stock_code=code, status="none", progress=0, has_summary=False)
    cached = db.scalar(
        select(business_ingest.BusinessOverviewCache).where(
            business_ingest.BusinessOverviewCache.stock_code == code
        )
    )
    has_summary = bool(cached and cached.payload and cached.payload.get("research_summary"))
    return ResearchStatus(
        stock_code=code,
        status=job.status,
        progress=job.progress,
        error=job.error,
        has_summary=has_summary,
    )
