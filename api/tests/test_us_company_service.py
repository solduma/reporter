"""US 종목 서비스 오케스트레이션 테스트 — SEC/네이버 어댑터를 목킹(네트워크 미사용)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, UsFinancial
from app.services import us_company_service

_FIXTURE = Path(__file__).parent / "fixtures" / "sec_nvda_facts.json"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[UsFinancial.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def _mock_adapters(monkeypatch):
    facts = json.loads(_FIXTURE.read_text())

    class _Q:
        close = "200.0"
        change_ratio = "1.5"
        rising = True

    monkeypatch.setattr(us_company_service.sec, "resolve_cik", lambda s, t: 1045810)
    monkeypatch.setattr(us_company_service.sec, "company_name", lambda s, t: "NVIDIA CORP")
    monkeypatch.setattr(us_company_service.sec, "fetch_company_facts", lambda s, c: facts)
    monkeypatch.setattr(
        us_company_service.us_market, "resolve_us_symbol", lambda t, session=None: ("NVDA.O", _Q())
    )


def test_get_financials_computes_and_caches(db, _mock_adapters):
    row = us_company_service.get_financials(db, "nvda")  # 소문자도 대문자로
    assert row is not None
    assert row.ticker == "NVDA"
    assert row.per and row.per > 0
    assert row.roe and row.roe > 0
    assert row.market_cap and row.market_cap > 0  # 종가 200 x 주식수
    # 캐시: 두 번째 호출은 같은 행(TTL 내), SEC 재조회 안 함.
    again = us_company_service.get_financials(db, "NVDA")
    assert again.updated_at == row.updated_at


def test_get_financials_unknown_ticker_returns_none(db, monkeypatch):
    monkeypatch.setattr(us_company_service.sec, "resolve_cik", lambda s, t: None)
    assert us_company_service.get_financials(db, "ZZZZ") is None


def test_quote_resolves_symbol(monkeypatch):
    class _Q:
        close = "210.96"
        change_ratio = "4.03"
        rising = True

    monkeypatch.setattr(
        us_company_service.us_market, "resolve_us_symbol", lambda t, session=None: ("NVDA.O", _Q())
    )
    monkeypatch.setattr(us_company_service.sec, "company_name", lambda s, t: "NVIDIA CORP")
    q = us_company_service.quote("nvda")
    assert q.ticker == "NVDA"
    assert q.naver_symbol == "NVDA.O"
    assert q.close == 210.96


def test_quote_none_when_symbol_unresolved(monkeypatch):
    monkeypatch.setattr(us_company_service.us_market, "resolve_us_symbol", lambda t, session=None: None)
    assert us_company_service.quote("ZZZZ") is None
