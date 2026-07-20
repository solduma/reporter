"""주담(IR) 인터뷰 전략 라우터 — enqueue·상태폴링·결과·목록.

딥다이브 완료 후 별도 호흡으로 도는 독립 파이프라인. 무거운 에이전틱 생성은 worker(DB 폴링 큐)가
실행하고, 라우터는 job 을 큐에 넣고 상태·결과를 돌려준다(ORM 직접 접근 대신 서비스 경유).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import IrInterviewListItem, IrInterviewReportOut, IrInterviewStatus
from app.services import company_service, ir_interview

router = APIRouter(prefix="/api/ir-interview", tags=["ir-interview"])


def _status(code: str, job, has: bool) -> IrInterviewStatus:
    if job is None:
        return IrInterviewStatus(stock_code=code, status="none", progress=0, has_report=has)
    return IrInterviewStatus(
        stock_code=code, status=job.status, progress=job.progress, error=job.error, has_report=has,
    )


@router.get("", response_model=list[IrInterviewListItem])
def list_ir_interviews(db: Session = Depends(get_session)) -> list[IrInterviewListItem]:
    """생성된 주담 전략 목록(최신순) — 최상단 메뉴 목록."""
    return [
        IrInterviewListItem(
            stock_code=r.stock_code,
            stock_name=company_service.resolve_stock_name(db, r.stock_code),
            total_questions=r.total_questions,
            as_of=r.as_of,
        )
        for r in ir_interview.list_reports(db)
    ]


@router.post("/{code}", response_model=IrInterviewStatus)
def request_ir_interview(code: str, db: Session = Depends(get_session)) -> IrInterviewStatus:
    """주담 전략 생성 요청(큐 enqueue). 진행 중이면 그 job 상태 반환(중복 방지)."""
    job = ir_interview.enqueue(db, code)
    has = ir_interview.get_report(db, code) is not None
    return _status(code, job, has)


@router.get("/{code}/status", response_model=IrInterviewStatus)
def ir_interview_status(code: str, db: Session = Depends(get_session)) -> IrInterviewStatus:
    """주담 전략 진행 상태(프론트 폴링). job 없으면 status=none."""
    job = ir_interview.latest_job(db, code)
    has = ir_interview.get_report(db, code) is not None
    return _status(code, job, has)


@router.delete("/{code}", status_code=204)
def delete_ir_interview(code: str, db: Session = Depends(get_session)) -> None:
    """주담 전략 삭제(결과 + 관련 job). 목록·상세의 개별 삭제용. 없어도 204(멱등)."""
    ir_interview.delete_report(db, code)


@router.get("/{code}", response_model=IrInterviewReportOut)
def ir_interview_report(code: str, db: Session = Depends(get_session)) -> IrInterviewReportOut:
    """주담 전략 결과(아이템→질문 트리). 없으면 빈 결과(strategy=None)."""
    rep = ir_interview.get_report(db, code)
    return IrInterviewReportOut(
        stock_code=code,
        stock_name=company_service.resolve_stock_name(db, code),
        model=rep.model if rep else None,
        strategy=rep.strategy_json if rep else None,
        total_questions=rep.total_questions if rep else 0,
        as_of=rep.as_of if rep else None,
    )
