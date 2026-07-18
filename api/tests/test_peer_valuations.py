"""peer_valuations — ev_ebitda(연간)·psr(분기)가 다른 period 에 있어도 각각 최신값을 채운다."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Financial
from app.services import company_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Financial.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _fin(code, period, *, ev=None, psr=None):
    return Financial(
        stock_code=code, period=period, is_estimate=False, ev_ebitda=ev, psr=psr
    )


def test_ev_and_psr_from_different_periods(db):
    # ev_ebitda 는 연간(.12)에만, psr 은 분기에만 있어도 둘 다 채워진다(구 버그: 하나만 채움).
    db.add_all([
        _fin("000001", "2026.03", psr=2.5),   # 최신 분기: psr 만
        _fin("000001", "2025.12", ev=7.3),    # 연간: ev_ebitda 만
    ])
    db.commit()
    out = company_service.peer_valuations(db, ["000001"])
    assert out["000001"] == ("7.3", "2.50")  # ev·psr 둘 다


def test_latest_value_per_metric(db):
    # 각 지표는 가장 최신 period 값으로 채운다.
    db.add_all([
        _fin("000002", "2026.03", psr=3.0),
        _fin("000002", "2025.12", psr=2.0, ev=5.0),  # 더 과거 → psr 은 2026.03 이 이김
    ])
    db.commit()
    out = company_service.peer_valuations(db, ["000002"])
    assert out["000002"] == ("5.0", "3.00")


def test_no_values_excluded(db):
    # ev·psr 둘 다 없는 종목은 결과에서 제외.
    db.add(_fin("000003", "2026.03"))
    db.commit()
    assert company_service.peer_valuations(db, ["000003"]) == {}


def test_empty_codes(db):
    assert company_service.peer_valuations(db, []) == {}
