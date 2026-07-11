"""산업 흐름 페이지 조회 서비스 — 라우터가 쓰던 산업/센티먼트/섹터종목 쿼리를 응용 계층으로.

라우터는 결과 ORM 행을 DTO 로 매핑만 한다. 섹터 종목 시세는 유니버스 스냅샷에서 조회.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Report, ReportAnalysis, UniverseSnapshot
from app.services import sector_ingest, universe_ingest


def industry_counts(db: Session) -> list[tuple[str, int]]:
    """industry 리포트 수(analysis 있는 것만) 내림차순. 센티먼트 시계열과 카운트 정합."""
    return list(
        db.execute(
            select(Report.industry_name, func.count(Report.id))
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.category == "industry", Report.industry_name.is_not(None))
            .group_by(Report.industry_name)
            .order_by(func.count(Report.id).desc())
        ).all()
    )


def sentiment_rows(
    db: Session, name: str, from_: date | None, to: date | None
) -> list[tuple[Report, ReportAnalysis]]:
    """산업의 (Report, ReportAnalysis) 쌍을 발행일 오름차순. 기간 필터 옵션."""
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
    return list(db.execute(stmt).all())


def kr_sector_stock_rows(db: Session, industry: str, codes: list[str]) -> list[tuple]:
    """최신 유니버스 스냅샷에서 해당 종목들의 (코드,명,종가,등락,시총,거래대금)."""
    as_of = universe_ingest.latest_snapshot_date(db)
    return list(
        db.execute(
            select(
                UniverseSnapshot.stock_code,
                UniverseSnapshot.stock_name,
                UniverseSnapshot.close_price,
                UniverseSnapshot.change_pct,
                UniverseSnapshot.market_cap,
                UniverseSnapshot.trading_value,
            ).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_code.in_(codes),
            )
        ).all()
    )


def sector_stock_codes(db: Session, industry: str) -> list[str]:
    """산업명 → 섹터 소속 종목코드(sector_ingest 위임)."""
    return sector_ingest.sector_stock_codes(db, industry)
