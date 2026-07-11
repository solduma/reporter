"""Today's Brew 조회 서비스 — 라우터가 쓰던 시황·리포트 쿼리를 응용 계층으로.

라우터는 이 서비스가 돌려준 ORM 행을 DTO 로 매핑만 한다(데이터 접근은 여기). PDF 원본은
storage 어댑터를 통해 가져온다.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyMarketInfo, Report


def latest_report_date(db: Session, category: str) -> date | None:
    """해당 카테고리의 최신 발행일. 카테고리마다 최신일이 달라 분리 산출."""
    return db.scalar(
        select(Report.published_date)
        .where(Report.category == category)
        .order_by(Report.published_date.desc())
        .limit(1)
    )


def market_info(db: Session, date_: date | None) -> DailyMarketInfo | None:
    """일자 시황(없으면 최신). 없으면 None."""
    stmt = select(DailyMarketInfo)
    stmt = (
        stmt.where(DailyMarketInfo.market_date == date_)
        if date_
        else stmt.order_by(DailyMarketInfo.market_date.desc())
    )
    return db.scalars(stmt.limit(1)).first()


def reports_for(db: Session, category: str, target: date) -> list[Report]:
    """카테고리·발행일의 리포트를 조회수 내림차순으로."""
    return list(
        db.scalars(
            select(Report)
            .where(Report.category == category, Report.published_date == target)
            .order_by(Report.views.desc())
        ).all()
    )


def get_report(db: Session, report_id: int) -> Report | None:
    return db.get(Report, report_id)
