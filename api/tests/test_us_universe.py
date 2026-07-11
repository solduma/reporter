"""US 유니버스 소스 파서 + 인제스트 단위 테스트(네트워크 목킹)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.adapters.external import us_universe as source
from app.db.models import Base, PriceCandle, UsUniverse
from app.services import us_universe_ingest as ing


def test_num_parses_naver_formats():
    assert source._num("32.26배") == 32.26
    assert source._num("26.14%") == 26.14
    assert source._num("1,234.5") == 1234.5
    assert source._num("N/A") is None
    assert source._num(None) is None


def test_won_eok_usd_korean_units():
    assert source._won_eok_usd("310억 USD") == 310e8
    assert source._won_eok_usd("5조 1,052억 USD") == 5e12 + 1052e8
    assert source._won_eok_usd(None) is None


@pytest.fixture
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[UsUniverse.__table__, PriceCandle.__table__])
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_snapshot_upserts_and_skips_no_marketcap(db, monkeypatch):
    rows = {
        "NVDA": source.UsUniverseRow(
            ticker="NVDA", naver_symbol="NVDA.O", name="NVIDIA", exchange="NASDAQ",
            sector="Tech", close_price=210.0, change_pct=4.0, market_cap=5.1e12,
            trading_value=31e9, per=32.26, pbr=26.14, eps=6.54, high_52w=236.5, low_52w=161.6,
        ),
        "BADX": None,  # 심볼 미해석 → skip
    }
    monkeypatch.setattr(source, "seed_tickers", lambda session=None: [("NVDA", "Tech"), ("BADX", None)])
    monkeypatch.setattr(source, "fetch_row", lambda t, sec, session=None: rows.get(t))

    res = ing.snapshot_us_universe(db, snapshot_date=date(2026, 7, 12))
    assert res == {"seeded": 2, "saved": 1, "skipped": 1}
    saved = db.scalars(select(UsUniverse)).all()
    assert len(saved) == 1
    assert saved[0].ticker == "NVDA" and saved[0].exchange == "NASDAQ"
    assert saved[0].market_cap == 5.1e12


def test_snapshot_idempotent_reupsert(db, monkeypatch):
    row = source.UsUniverseRow(
        ticker="AAPL", naver_symbol="AAPL.O", name="Apple", exchange="NASDAQ", sector="Tech",
        close_price=315.0, change_pct=1.0, market_cap=4.6e12, trading_value=10e9,
        per=38.0, pbr=50.0, eps=8.0, high_52w=None, low_52w=None,
    )
    monkeypatch.setattr(source, "seed_tickers", lambda session=None: [("AAPL", "Tech")])
    monkeypatch.setattr(source, "fetch_row", lambda t, sec, session=None: row)
    ing.snapshot_us_universe(db, snapshot_date=date(2026, 7, 12))
    ing.snapshot_us_universe(db, snapshot_date=date(2026, 7, 12))  # 같은 날 재실행
    assert len(db.scalars(select(UsUniverse)).all()) == 1  # upsert(중복 행 없음)
