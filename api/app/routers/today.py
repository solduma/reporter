"""Today's Brew 페이지용 라우터."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.adapters.storage import minio_store
from app.db.session import get_session
from app.schemas import MarketBrief, ReportCard
from app.services import today_service

router = APIRouter(prefix="/api", tags=["today"])


@router.get("/today/market", response_model=MarketBrief)
def today_market(
    date_: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_session),
) -> MarketBrief:
    row = today_service.market_info(db, date_)
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
    target = date_ or today_service.latest_report_date(db, category)
    if not target:
        return []
    rows = today_service.reports_for(db, category, target)
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
    report = today_service.get_report(db, report_id)
    if not report or not report.pdf_object_key:
        raise HTTPException(status_code=404, detail="PDF 없음")
    data = minio_store.get_pdf(report.pdf_object_key)
    if data is None:
        raise HTTPException(status_code=404, detail="PDF 객체 없음")
    return Response(content=data, media_type="application/pdf")
