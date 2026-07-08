"""수동 수집 트리거. 스케줄러(2단계) 도입 전 데모·백필용."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.services import ingest

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/ingest")
def trigger_ingest(
    date_: str | None = Query(default=None, alias="date", description="YY.MM.DD, 기본 오늘"),
    db: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    reports = ingest.ingest_reports(db, settings, target_date=date_)
    market = ingest.build_market_brief(db, settings, target_date=date_)
    return {"reports_ingested": reports, "market_brief": bool(market)}
