"""섹터 로테이션·시황 대시보드 조회 서비스 — 라우터가 쓰던 리서치 쿼리를 응용 계층으로.

리포트 기반 섹터 집계(센티먼트·커버리지)와 대시보드용 시황/무역 스파크 쿼리. 스코어링은
domain.analysis_scoring, 시세 스냅샷은 market_quote 가 담당하고 여기선 순수 조회만 한다.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import DailyMarketInfo, Report, ReportAnalysis, Sentiment, TradeStat

_SENT_CASE = case(
    (ReportAnalysis.sentiment == Sentiment.BUY, 1.0),
    (ReportAnalysis.sentiment == Sentiment.SELL, -1.0),
    else_=0.0,
)


def sector_rows(db: Session, since: date) -> list[tuple[str, int, float]]:
    """산업별 (섹터, 리포트수, 평균 센티먼트). since 이후 발행 industry 리포트."""
    return list(
        db.execute(
            select(
                Report.industry_name,
                func.count(Report.id),
                func.avg(_SENT_CASE),
            )
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(
                Report.category == "industry",
                Report.industry_name.is_not(None),
                Report.published_date >= since,
            )
            .group_by(Report.industry_name)
        ).all()
    )


def latest_market_info(db: Session) -> DailyMarketInfo | None:
    """가장 최근 일자 시황(대시보드 요약)."""
    return db.scalars(
        select(DailyMarketInfo).order_by(DailyMarketInfo.market_date.desc()).limit(1)
    ).first()


def trade_spark(db: Session, limit: int = 5) -> list[dict]:
    """HS 품목별 최신 수출액 스파크(있으면). [{hs, period, export_usd}]."""
    rows = db.execute(
        select(TradeStat.hs_code, func.max(TradeStat.period)).group_by(TradeStat.hs_code)
    ).all()
    out: list[dict] = []
    for hs, period in rows[:limit]:
        latest = db.scalar(
            select(TradeStat.export_usd).where(
                TradeStat.hs_code == hs, TradeStat.period == period
            )
        )
        out.append({"hs": hs, "period": period, "export_usd": latest})
    return out
