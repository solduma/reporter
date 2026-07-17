"""종목 딥다이브 라우터 — enqueue·상태폴링·결과 조회·HITL 인풋.

무거운 5단계 파이프라인은 worker(DB 폴링 큐)가 실행하고, 라우터는 job 을 큐에 넣고 상태·결과를
돌려준다. 데이터 접근은 services/deepdive.orchestrator 경유(라우터는 ORM 직접 접근 금지 계약).
밸류에이션 직전 HITL: paused job 에 인풋을 제출(POST /{code}/hitl)하면 검증 후 재개한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import (
    DeepDiveReportOut,
    DeepDiveSharedReport,
    DeepDiveShareOut,
    DeepDiveStatus,
    HitlInput,
)
from app.services.deepdive import orchestrator, share

router = APIRouter(prefix="/api/deepdive", tags=["deepdive"])


def _status(code: str, job, has: bool) -> DeepDiveStatus:
    """job → 상태 DTO(HITL 필드 포함). job 없으면 status=none."""
    if job is None:
        return DeepDiveStatus(stock_code=code, status="none", current_stage=0, progress=0, has_report=has)
    return DeepDiveStatus(
        stock_code=code, status=job.status, current_stage=job.current_stage,
        progress=job.progress, error=job.error, has_report=has,
        hitl_pending=job.hitl_pending, hitl_prompt=job.hitl_prompt,
    )


@router.post("/{code}", response_model=DeepDiveStatus)
def request_deepdive(code: str, db: Session = Depends(get_session)) -> DeepDiveStatus:
    """딥다이브 생성 요청(큐 enqueue). 진행 중이면 그 job 상태를 반환(중복 방지)."""
    job = orchestrator.enqueue(db, code)
    has = orchestrator.get_report(db, code) is not None
    return _status(code, job, has)


@router.get("/{code}/status", response_model=DeepDiveStatus)
def deepdive_status(code: str, db: Session = Depends(get_session)) -> DeepDiveStatus:
    """딥다이브 진행 상태(프론트 폴링). job 없으면 status=none."""
    job = orchestrator.latest_job(db, code)
    has = orchestrator.get_report(db, code) is not None
    return _status(code, job, has)


@router.post("/{code}/hitl", response_model=DeepDiveStatus)
def submit_hitl(code: str, body: HitlInput, db: Session = Depends(get_session)) -> DeepDiveStatus:
    """밸류에이션 직전 paused 상태에 사용자 인풋을 제출해 재개. paused job 없으면 409."""
    job = orchestrator.submit_hitl(db, code, body.input)
    if job is None:
        raise HTTPException(status_code=409, detail="일시정지된 딥다이브가 없습니다.")
    has = orchestrator.get_report(db, code) is not None
    return _status(code, job, has)


@router.get("/{code}", response_model=DeepDiveReportOut | None)
def deepdive_report(code: str, db: Session = Depends(get_session)) -> DeepDiveReportOut | None:
    """완료된 딥다이브 보고서(단계별 JSON + 서술 본문). 없으면 null."""
    rep = orchestrator.get_report(db, code)
    if rep is None:
        return None
    return share.report_to_out(rep)


@router.post("/{code}/share", response_model=DeepDiveShareOut)
def create_deepdive_share(code: str, db: Session = Depends(get_session)) -> DeepDiveShareOut:
    """현 보고서를 30분짜리 무인증 공유 스냅샷으로 굳힌다. 보고서 없으면 404."""
    created = share.create_share(db, code)
    if created is None:
        raise HTTPException(status_code=404, detail="공유할 딥다이브 보고서가 없습니다.")
    return DeepDiveShareOut(token=created.token, expires_at=created.expires_at)


@router.get("/share/{token}", response_model=DeepDiveSharedReport)
def get_shared_deepdive(token: str, db: Session = Depends(get_session)) -> DeepDiveSharedReport:
    """무인증 공유 페이지가 조회하는 스냅샷. 없거나 만료(30분 경과)면 410."""
    found = share.get_valid_share(db, token)
    if found is None:
        raise HTTPException(status_code=410, detail="만료되었거나 존재하지 않는 공유 링크입니다.")
    return DeepDiveSharedReport(
        stock_code=found.stock_code,
        stock_name=found.stock_name,
        report=DeepDiveReportOut.model_validate(found.payload_json),
        created_at=found.created_at,
        expires_at=found.expires_at,
    )
