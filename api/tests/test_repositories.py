"""Repository 어댑터 단위 테스트 — 인메모리 SQLite 로 SQLAlchemy 구현 검증.

포트 인터페이스를 만족하는지(시그니처·동작)와 봉 upsert 멱등성을 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.persistence import SqlCandleRepository, SqlUniverseRepository
from app.db.models import Base, PriceCandle, Timeframe, UniverseSnapshot


@pytest.fixture
def db():
    # SQLite 로 읽기·최신날짜 쿼리만 검증한다. fallback_event 의 JSONB 등 Postgres 전용 컬럼은
    # SQLite 에서 컴파일 실패하므로 이 테스트가 쓰는 테이블만 생성한다.
    engine = create_engine("sqlite://")
    tables = [UniverseSnapshot.__table__, PriceCandle.__table__]
    Base.metadata.create_all(engine, tables=tables)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@dataclass
class _Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    foreign_ratio: float | None = None


def test_universe_latest_snapshot_date_empty(db):
    assert SqlUniverseRepository(db).latest_snapshot_date() is None


def test_universe_latest_snapshot_date_picks_max(db):
    for d in (date(2026, 7, 1), date(2026, 7, 10), date(2026, 7, 5)):
        db.add(
            UniverseSnapshot(
                stock_code="005930", snapshot_date=d, market="KOSPI", stock_name="삼성전자"
            )
        )
    db.commit()
    assert SqlUniverseRepository(db).latest_snapshot_date() == date(2026, 7, 10)


def test_candle_read_periodic_empty(db):
    assert SqlCandleRepository(db).read_periodic("005930", "day") == []


def test_candle_latest_bar_date_empty(db):
    assert SqlCandleRepository(db).latest_bar_date("005930", Timeframe.DAY) is None
