"""latest_valuation 테스트 — 분기 최신 행에 없는 연간 지표(EV/EBITDA·배당) 보정."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Financial
from app.services import company_service


@pytest.fixture
def db():
    # 격리 in-memory SQLite. Financial 만 있으면 되는 순수 조회 테스트.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Financial.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _fin(**kw):
    return Financial(stock_code="000000", is_estimate=False, **kw)


def test_ev_ebitda_backfilled_from_annual(db):
    # 분기 최신(2026.03)엔 ev_ebitda 없고 per/pbr 있음. 연간(2025.12)에만 ev_ebitda.
    db.add_all([
        _fin(period="2025.12", per=10, pbr=1.0, ev_ebitda=8.5, div_yield=2.0),
        _fin(period="2026.03", per=12, pbr=1.2),  # 최신 분기 — ev_ebitda·div_yield 결측
    ])
    db.commit()
    val = company_service.latest_valuation(db, "000000")
    assert val.period == "2026.03"  # 최신 분기 선택
    assert val.ev_ebitda == 8.5  # 연간에서 보정
    assert val.div_yield == 2.0  # 연간에서 보정(기존 동작)


def test_no_annual_ev_ebitda_stays_none(db):
    db.add_all([_fin(period="2026.03", per=12, pbr=1.2)])
    db.commit()
    val = company_service.latest_valuation(db, "000000")
    assert val.ev_ebitda is None  # 연간에도 없으면 그대로 None


def test_quarter_ev_ebitda_not_overwritten(db):
    # 최신 행에 이미 ev_ebitda 있으면 연간 보정 안 함(덮어쓰기 방지).
    db.add_all([
        _fin(period="2025.12", per=10, pbr=1.0, ev_ebitda=8.5),
        _fin(period="2026.03", per=12, pbr=1.2, ev_ebitda=9.9),
    ])
    db.commit()
    val = company_service.latest_valuation(db, "000000")
    assert val.ev_ebitda == 9.9  # 최신 행 값 유지
