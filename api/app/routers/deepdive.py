"""종목 딥다이브 라우터 — enqueue·상태폴링·결과 조회.

무거운 5단계 파이프라인은 worker(DB 폴링 큐)가 실행하고, 라우터는 job 을 큐에 넣고 상태·결과를
돌려준다. 데이터 접근은 services/deepdive.orchestrator 경유(라우터는 ORM 직접 접근 금지 계약).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import DeepDiveReportOut, DeepDiveStatus
from app.services.deepdive import orchestrator

router = APIRouter(prefix="/api/deepdive", tags=["deepdive"])


@router.post("/{code}", response_model=DeepDiveStatus)
def request_deepdive(code: str, db: Session = Depends(get_session)) -> DeepDiveStatus:
    """딥다이브 생성 요청(큐 enqueue). 진행 중이면 그 job 상태를 반환(중복 방지)."""
    job = orchestrator.enqueue(db, code)
    has = orchestrator.get_report(db, code) is not None
    return DeepDiveStatus(
        stock_code=code, status=job.status, current_stage=job.current_stage,
        progress=job.progress, error=job.error, has_report=has,
    )


@router.get("/{code}/status", response_model=DeepDiveStatus)
def deepdive_status(code: str, db: Session = Depends(get_session)) -> DeepDiveStatus:
    """딥다이브 진행 상태(프론트 폴링). job 없으면 status=none."""
    job = orchestrator.latest_job(db, code)
    has = orchestrator.get_report(db, code) is not None
    if job is None:
        return DeepDiveStatus(stock_code=code, status="none", current_stage=0, progress=0, has_report=has)
    return DeepDiveStatus(
        stock_code=code, status=job.status, current_stage=job.current_stage,
        progress=job.progress, error=job.error, has_report=has,
    )


@router.get("/{code}", response_model=DeepDiveReportOut | None)
def deepdive_report(code: str, db: Session = Depends(get_session)) -> DeepDiveReportOut | None:
    """완료된 딥다이브 보고서(단계별 JSON + 서술 본문). 없으면 null."""
    rep = orchestrator.get_report(db, code)
    if rep is None:
        return None
    return DeepDiveReportOut(
        stock_code=rep.stock_code, model=rep.model,
        overview=rep.overview_json, redflags=rep.redflags_json, business=rep.business_json,
        thesis=rep.thesis_json, valuation=rep.valuation_json,
        narrative_md=rep.narrative_md, verdict=rep.verdict, upside_pct=rep.upside_pct, as_of=rep.as_of,
    )
