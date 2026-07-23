"""수동 수집 트리거. 스케줄러(2단계) 도입 전 데모·백필용."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.services import broadcast_ingest, company_service, growth_ingest, ingest, universe_ingest

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/ingest")
def trigger_ingest(
    date_: str | None = Query(default=None, alias="date", description="YY.MM.DD, 기본 오늘"),
    db: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    reports = ingest.ingest_reports(db, settings, target_date=date_)
    market = ingest.build_market_brief(db, settings, target_date=date_)
    broadcasts = broadcast_ingest.ingest_broadcasts(db, settings)
    return {
        "reports_ingested": reports,
        "market_brief": bool(market),
        "broadcasts_ingested": broadcasts,
    }


@router.post("/universe/snapshot")
def trigger_universe_snapshot(
    markets: str = Query(default="KOSDAQ,KOSPI", description="쉼표구분 시장 목록"),
    db: Session = Depends(get_session),
) -> dict:
    market_tuple = tuple(m.strip() for m in markets.split(",") if m.strip())
    rows = universe_ingest.snapshot_universe(db, datetime.now().date(), market_tuple)
    return {"rows_upserted": rows}


@router.post("/growth/batch")
def trigger_growth_batch(
    limit: int | None = Query(default=None, description="처리 종목 수 상한(테스트용)"),
    db: Session = Depends(get_session),
) -> dict:
    return growth_ingest.run_growth_batch(db, limit=limit)


@router.post("/financial-statements/backfill-ontology")
def trigger_fs_ontology_backfill(
    code: str | None = Query(default=None, description="종목 코드(미지정 시 전체)"),
    limit: int | None = Query(default=None, description="처리 행 수 상한"),
    db: Session = Depends(get_session),
) -> dict:
    """기존 FinancialStatement 행의 ontology_id in-place 보강(DART 호출 없음)."""
    updated = company_service.backfill_financial_statement_ontology_id(db, code=code, limit=limit)
    return {"rows_updated": updated}
