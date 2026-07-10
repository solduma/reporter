"""Today's Brew 페이지용 라우터."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyMarketInfo, Report
from app.db.session import get_session
from app.schemas import MarketBrief, ReportCard
from app.storage import minio_store

router = APIRouter(prefix="/api", tags=["today"])


def _latest_date_for(db: Session, category: str) -> date | None:
    """해당 카테고리의 최신 발행일. 카테고리마다 최신일이 달라도 컬럼이 비지 않도록 분리 산출."""
    return db.scalar(
        select(Report.published_date)
        .where(Report.category == category)
        .order_by(Report.published_date.desc())
        .limit(1)
    )


@router.get("/today/market", response_model=MarketBrief)
def today_market(
    date_: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_session),
) -> MarketBrief:
    stmt = select(DailyMarketInfo)
    stmt = (
        stmt.where(DailyMarketInfo.market_date == date_)
        if date_
        else stmt.order_by(DailyMarketInfo.market_date.desc())
    )
    row = db.scalars(stmt.limit(1)).first()
    if not row:
        return MarketBrief(market_date=None, summary="")
    return MarketBrief(
        market_date=row.market_date,
        summary=row.summary,
        phase=row.phase or "",
        updated_at=row.updated_at,
    )


@router.get("/today/reports", response_model=list[ReportCard])
def today_reports(
    category: str = Query(pattern="^(company|industry)$"),
    date_: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_session),
) -> list[ReportCard]:
    target = date_ or _latest_date_for(db, category)
    if not target:
        return []
    rows = db.scalars(
        select(Report)
        .where(Report.category == category, Report.published_date == target)
        .order_by(Report.views.desc())
    ).all()
    return [
        ReportCard(
            id=r.id,
            category=r.category,
            title=r.title,
            broker=r.broker,
            name=r.stock_name or r.industry_name,
            summary=(r.analysis.summary if r.analysis else ""),
            sentiment=(r.analysis.sentiment.value if r.analysis else "HOLD"),
            rationale=(r.analysis.rationale if r.analysis else ""),
            published_date=r.published_date,
            has_pdf=bool(r.pdf_object_key),
        )
        for r in rows
    ]


@router.get("/reports/{report_id}/pdf")
def report_pdf(report_id: int, db: Session = Depends(get_session)) -> Response:
    report = db.get(Report, report_id)
    if not report or not report.pdf_object_key:
        raise HTTPException(status_code=404, detail="PDF 없음")
    data = minio_store.get_pdf(report.pdf_object_key)
    if data is None:
        raise HTTPException(status_code=404, detail="PDF 객체 없음")
    return Response(content=data, media_type="application/pdf")
