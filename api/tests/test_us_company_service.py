"""US 종목 서비스 오케스트레이션 테스트 — SEC/네이버 어댑터를 목킹(네트워크 미사용)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, SyncState, UsFinancial, UsUniverse
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
def db_full():
    """유니버스·마커 포함(백필 테스트용)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[UsFinancial.__table__, UsUniverse.__table__, SyncState.__table__]
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_universe(db, tickers):
    for t in tickers:
        db.add(UsUniverse(snapshot_date=date(2026, 7, 18), ticker=t, naver_symbol=t, name=t))
    db.commit()


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


def test_financials_backfill_marks_per_success(db_full, _mock_adapters):
    # 유니버스 종목을 백필해 per 산출 성공분만 완료 마킹한다.
    _seed_universe(db_full, ["NVDA", "AAPL"])
    result = us_company_service.run_financials_backfill(db_full, per_run=10)
    assert result["done"] == 2  # 둘 다 목킹된 facts 로 per 산출
    marked = set(
        db_full.scalars(
            select(SyncState.stock_code).where(SyncState.domain == "us_financials_10y")
        ).all()
    )
    assert marked == {"NVDA", "AAPL"}


def test_financials_backfill_skips_already_done(db_full, _mock_adapters):
    # 이미 마킹된 종목은 pending 에서 제외(재조회 안 함).
    _seed_universe(db_full, ["NVDA", "AAPL"])
    us_company_service.sync_state.mark(db_full, "us_financials_10y", "NVDA")
    db_full.commit()
    result = us_company_service.run_financials_backfill(db_full, per_run=10)
    assert result["done"] == 1  # AAPL 만 신규 처리


def test_financials_backfill_reconciles_orphan_markers(db_full):
    # 재무 행(per)이 있는데 마커가 없으면 SEC 재조회 없이 마커 복원.
    _seed_universe(db_full, ["NVDA"])
    db_full.add(UsFinancial(ticker="NVDA", name="NVIDIA", per=30.0))
    db_full.commit()
    restored = us_company_service._reconcile_markers(db_full, ["NVDA"], set())
    assert restored == 1
