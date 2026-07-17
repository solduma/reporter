"""보고서 원문 백필 단위 테스트 — 대상 기간 산정·기간 문자열·주식수 역산 폴백."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, UniverseSnapshot
from app.services import report_ingest as ri


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[UniverseSnapshot.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _snap(code, d, market_cap, close):
    return UniverseSnapshot(
        snapshot_date=d, stock_code=code, market="KOSDAQ",
        stock_name="X", stock_type="stock", market_cap=market_cap, close_price=close,
    )


def test_shares_from_snapshot_derives_from_marketcap(db):
    # 시총 225,179,307,340 ÷ 종가 9,230 ≈ 24,396,458 주.
    db.add(_snap("071200", date(2026, 7, 17), 225179307340, 9230))
    db.commit()
    assert ri._shares_from_snapshot(db, "071200") == round(225179307340 / 9230)


def test_shares_from_snapshot_uses_latest(db):
    db.add_all([
        _snap("000100", date(2026, 7, 10), 1000, 10),  # 옛 스냅샷
        _snap("000100", date(2026, 7, 17), 2000, 10),  # 최신 → 200주
    ])
    db.commit()
    assert ri._shares_from_snapshot(db, "000100") == 200


def test_shares_from_snapshot_none_when_missing(db):
    assert ri._shares_from_snapshot(db, "999999") is None
    # 시총 또는 종가 결측이면 역산 불가.
    db.add(_snap("000200", date(2026, 7, 17), None, 10))
    db.add(_snap("000300", date(2026, 7, 17), 1000, None))
    db.commit()
    assert ri._shares_from_snapshot(db, "000200") is None
    assert ri._shares_from_snapshot(db, "000300") is None


def test_target_reports_past_is_annual_only():
    # 2026-07 기준: 과거(≤2025)는 사업보고서만, 2026~ 는 반기/분기 추가.
    targets = ri._target_reports(date(2026, 7, 11))
    # 과거 연도는 annual 만.
    assert (2020, "annual") in targets
    assert (2020, "half") not in targets
    assert (2020, "quarter") not in targets
    # 2026 은 half·quarter 포함(annual 은 아직 미확정이라 제외 — year==today.year).
    assert (2026, "half") in targets
    assert (2026, "quarter") in targets
    assert (2026, "annual") not in targets


def test_target_reports_10yr_span():
    targets = ri._target_reports(date(2026, 7, 11))
    years = {y for y, _ in targets}
    assert min(years) == 2016  # 10년 전부터
    assert 2025 in years


def test_period_str():
    assert ri._period_str(2023, "annual") == "2023.12"
    assert ri._period_str(2026, "half") == "2026.06"
    assert ri._period_str(2026, "quarter") == "2026.03"
