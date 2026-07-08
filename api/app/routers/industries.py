"""산업 흐름 페이지용 라우터 — 산업별 발간일별 센티먼트 시계열."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Report, ReportAnalysis, Sentiment
from app.db.session import get_session
from app.schemas import IndustrySummary, ReportRef, SentimentPoint

router = APIRouter(prefix="/api/industries", tags=["industries"])

# 센티먼트 → 수치 (시계열 평균 산출용)
_SCORE = {Sentiment.BUY: 1.0, Sentiment.HOLD: 0.0, Sentiment.SELL: -1.0}


@router.get("", response_model=list[IndustrySummary])
def list_industries(db: Session = Depends(get_session)) -> list[IndustrySummary]:
    rows = db.execute(
        select(Report.industry_name, func.count(Report.id))
        .where(Report.category == "industry", Report.industry_name.is_not(None))
        .group_by(Report.industry_name)
        .order_by(func.count(Report.id).desc())
    ).all()
    return [IndustrySummary(industry=name, report_count=count) for name, count in rows]


@router.get("/{name}/sentiment", response_model=list[SentimentPoint])
def industry_sentiment(
    name: str,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[SentimentPoint]:
    stmt = (
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.category == "industry", Report.industry_name == name)
        .order_by(Report.published_date)
    )
    if from_:
        stmt = stmt.where(Report.published_date >= from_)
    if to:
        stmt = stmt.where(Report.published_date <= to)

    by_date: dict[date, list[tuple[Report, ReportAnalysis]]] = {}
    for report, analysis in db.execute(stmt).all():
        by_date.setdefault(report.published_date, []).append((report, analysis))

    points: list[SentimentPoint] = []
    for day in sorted(by_date):
        rows = by_date[day]
        avg = sum(_SCORE[a.sentiment] for _, a in rows) / len(rows)
        points.append(
            SentimentPoint(
                date=day,
                avg_sentiment=round(avg, 3),
                reports=[
                    ReportRef(
                        id=r.id,
                        title=r.title,
                        broker=r.broker,
                        sentiment=a.sentiment.value,
                        summary=a.summary,
                        read_url=r.read_url,
                        has_pdf=bool(r.pdf_object_key),
                    )
                    for r, a in rows
                ],
            )
        )
    return points
