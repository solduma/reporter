"""유니버스 스냅샷 적재 — marketValue 수집 결과를 오늘 날짜로 upsert."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import universe
from app.adapters.persistence import SqlUniverseRepository
from app.db.models import UniverseSnapshot
from app.ports.repositories import UniverseRepository

logger = logging.getLogger(__name__)


# 포트 공급자 seam — 기본은 SqlUniverseRepository, 테스트가 이 훅을 교체해 fake 주입 가능.
def _universe_repo(db: Session) -> UniverseRepository:
    return SqlUniverseRepository(db)


def latest_snapshot_date(db: Session) -> date | None:
    """가장 최근 유니버스 스냅샷 날짜(UniverseRepository 위임). 없으면 None.

    여러 라우터·서비스가 "오늘 기준 유니버스"를 잡을 때 쓰던 중복 쿼리를 이 헬퍼로 통일한다.
    """
    return _universe_repo(db).latest_snapshot_date()


def snapshot_universe(db: Session, snapshot_date: date, markets: tuple[str, ...] = ("KOSDAQ", "KOSPI")) -> int:
    """전 종목을 오늘 날짜 스냅샷으로 적재한다. 적재 행수를 반환한다(멱등 upsert)."""
    rows = universe.fetch_universe(markets)
    for r in rows:
        stmt = insert(UniverseSnapshot).values(
            snapshot_date=snapshot_date,
            stock_code=r.stock_code,
            market=r.market,
            stock_name=r.stock_name,
            stock_type=r.stock_type,
            close_price=r.close_price,
            change_pct=r.change_pct,
            market_cap=r.market_cap,
            trading_value=r.trading_value,
            three_month_rate=r.three_month_rate,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_universe",
            set_={
                "market": stmt.excluded.market,
                "stock_name": stmt.excluded.stock_name,
                "stock_type": stmt.excluded.stock_type,
                "close_price": stmt.excluded.close_price,
                "change_pct": stmt.excluded.change_pct,
                "market_cap": stmt.excluded.market_cap,
                "trading_value": stmt.excluded.trading_value,
                "three_month_rate": stmt.excluded.three_month_rate,
            },
        )
        db.execute(stmt)
    db.commit()
    logger.info("universe snapshot %s: %d rows", snapshot_date, len(rows))
    return len(rows)
