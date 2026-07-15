"""경제/실적 캘린더 라우터 — 과거(영향·이유) / 미래(기대치) 이벤트 조회.

데이터 접근·구간 계산·DTO 매핑은 calendar_ingest 서비스가 담당하고 여기선 쿼리 파라미터만 넘긴다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import CalendarView
from app.services import calendar_ingest

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("", response_model=CalendarView)
def get_calendar(
    region: str | None = Query(default=None, pattern="^(US|KR|GLOBAL)$"),
    kind: str | None = Query(default=None, pattern="^(macro|earnings|fomc|election|geo)$"),
    past_days: int = Query(default=60, ge=0, le=365),
    future_days: int = Query(default=90, ge=0, le=365),
    db: Session = Depends(get_session),
) -> CalendarView:
    return calendar_ingest.list_events(
        db, region=region, kind=kind, past_days=past_days, future_days=future_days
    )
