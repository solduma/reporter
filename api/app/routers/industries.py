"""산업 흐름 페이지용 라우터 — 산업별 발간일별 센티먼트 시계열."""

from __future__ import annotations

from datetime import date

import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Report, ReportAnalysis, Sentiment, TradeStat
from app.db.session import get_session
from app.schemas import IndustrySummary, ReportRef, SentimentPoint, TradePoint
from app.services import customs

router = APIRouter(prefix="/api/industries", tags=["industries"])

# 센티먼트 → 수치 (시계열 평균 산출용)
_SCORE = {Sentiment.BUY: 1.0, Sentiment.HOLD: 0.0, Sentiment.SELL: -1.0}


@router.get("", response_model=list[IndustrySummary])
def list_industries(db: Session = Depends(get_session)) -> list[IndustrySummary]:
    # 센티먼트 시계열과 동일하게 analysis 가 있는 리포트만 센다(카운트-플롯 정합성).
    rows = db.execute(
        select(Report.industry_name, func.count(Report.id))
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
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


# 별도 라우터: 무역통계는 /api/trade (산업 흐름 페이지 하단).
trade_router = APIRouter(prefix="/api/trade", tags=["trade"])

# 대표 품목 프리셋(HS 4자리). 산업 흐름과 연관 큰 품목 위주.
TRADE_PRESETS = {
    "8542": "반도체",
    "8471": "컴퓨터",
    "8517": "통신기기",
    "2710": "석유제품",
    "8703": "승용차",
    "8708": "자동차부품",
}


@trade_router.get("", response_model=list[TradePoint])
def trade_stats(
    hs: str = Query(default="8542", pattern=r"^\d{4,12}$", description="HS 코드(4~12자리)"),
    start: str = Query(..., pattern=r"^\d{6}$", description="시작 YYYYMM"),
    end: str = Query(..., pattern=r"^\d{6}$", description="종료 YYYYMM"),
    db: Session = Depends(get_session),
) -> list[TradePoint]:
    settings = get_settings()
    if settings.customs_api_key:
        fetched = customs.fetch_trade_by_hs(
            settings.customs_api_key, hs, start, end, requests.Session()
        )
        for m in fetched:
            stmt = insert(TradeStat).values(
                hs_code=hs,
                period=m.period,
                export_usd=m.export_usd,
                import_usd=m.import_usd,
                balance_usd=m.balance_usd,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_trade_stat",
                set_={
                    "export_usd": stmt.excluded.export_usd,
                    "import_usd": stmt.excluded.import_usd,
                    "balance_usd": stmt.excluded.balance_usd,
                },
            )
            db.execute(stmt)
        if fetched:
            db.commit()

    # 요청 [start, end] 윈도우만 반환. period 는 'YYYY.MM' 제로패딩이라 문자열 비교로 대소 판정.
    start_p, end_p = f"{start[:4]}.{start[4:]}", f"{end[:4]}.{end[4:]}"
    rows = db.scalars(
        select(TradeStat)
        .where(
            TradeStat.hs_code == hs,
            TradeStat.period.between(start_p, end_p),
        )
        .order_by(TradeStat.period)
    ).all()
    return [
        TradePoint(
            period=r.period,
            export_usd=r.export_usd,
            import_usd=r.import_usd,
            balance_usd=r.balance_usd,
        )
        for r in rows
    ]


@trade_router.get("/presets")
def trade_presets() -> dict:
    return TRADE_PRESETS
