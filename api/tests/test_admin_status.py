"""admin_status 적재 현황 — 최신 업데이트 내림차순 정렬 검증."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DailyMarketInfo, UniverseSnapshot
from app.services import admin_status


@pytest.fixture
def db(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(
        eng,
        tables=[UniverseSnapshot.__table__, DailyMarketInfo.__table__],
    )
    # db_status 는 12개 테이블 전부를 조회하므로, 이 테스트에선 관심 테이블(유니버스=date,
    # 시황요약=datetime)로 _DB_TABLES 를 축소해 정렬 로직만 검증한다.
    monkeypatch.setattr(admin_status, "_DB_TABLES", [
        ("유니버스", UniverseSnapshot, UniverseSnapshot.snapshot_date),
        ("시황요약", DailyMarketInfo, DailyMarketInfo.updated_at),
    ])
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_db_status_sorted_by_latest_desc(db):
    # date(유니버스)와 datetime(시황요약)이 섞여도 최신 업데이트 내림차순으로 정렬돼야 한다.
    db.add(UniverseSnapshot(
        snapshot_date=date(2026, 7, 15), stock_code="000660", market="KOSPI",
        stock_name="x", stock_type="stock", market_cap=1, trading_value=1,
    ))
    db.add(DailyMarketInfo(market_date=date(2026, 7, 12), summary="m",
                           updated_at=datetime(2026, 7, 12, 8, 0)))
    db.commit()
    rows = admin_status.db_status(db)
    # 유니버스(07-15) 가 시황요약(07-12) 보다 앞선다(내림차순).
    assert [r.name for r in rows] == ["유니버스", "시황요약"]


def test_db_status_missing_latest_sinks_to_bottom(db):
    # 데이터 없는 테이블(latest 없음)은 최하위로 밀린다(있는 것보다 아래).
    db.add(UniverseSnapshot(
        snapshot_date=date(2026, 7, 15), stock_code="000660", market="KOSPI",
        stock_name="x", stock_type="stock", market_cap=1, trading_value=1,
    ))
    db.commit()  # 시황요약은 비워둠 → latest 없음
    rows = admin_status.db_status(db)
    assert [r.name for r in rows] == ["유니버스", "시황요약"]  # 값 있는 유니버스가 위
    assert next(r for r in rows if r.name == "시황요약").latest == "—"
