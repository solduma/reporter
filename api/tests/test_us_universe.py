"""US 유니버스 소스 파서 + 인제스트 단위 테스트(네트워크 목킹)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.adapters.external import us_universe as source
from app.db.models import Base, PriceCandle, SyncState, UsUniverse
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
    Base.metadata.create_all(
        eng, tables=[UsUniverse.__table__, PriceCandle.__table__, SyncState.__table__]
    )
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


# ── US 일봉 백필(재개·momentum 재계산) ────────────────────────────────────
def _seed_us_row(db, sym: str, snap: date):
    db.add(UsUniverse(
        snapshot_date=snap, ticker=sym.split(".")[0], naver_symbol=sym, name=sym,
        exchange="NASDAQ", sector="Tech", market_cap=1e12,
    ))
    db.commit()


def _fake_candles(n: int, start_price: float, end_price: float):
    # n개 일봉(선형 가격). ts/open/high/low/close/volume — batch_upsert 가 읽는 필드.
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.adapters.market.naver import Candle

    base = _dt(2026, 1, 1)
    out = []
    for i in range(n):
        px = start_price + (end_price - start_price) * i / max(1, n - 1)
        out.append(Candle(ts=base + _td(days=i), open=px, high=px, low=px, close=px, volume=1000))
    return out


def test_candle_backfill_resumes_and_marks(db, monkeypatch):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import PriceCandle as PC

    snap = date(2026, 7, 16)
    _seed_us_row(db, "AAA.O", snap)
    _seed_us_row(db, "BBB.O", snap)
    # BBB 는 이미 백필 완료로 마킹 → 재처리 대상 아님(재개).
    from app.services import sync_state

    sync_state.mark(db, "us_candle_10y", "BBB.O")
    db.commit()

    fetched: list[str] = []

    def _fake_fetch(sym, tf, start, end, session):
        fetched.append(sym)
        return _fake_candles(64, 100.0, 130.0)  # +30%

    # 실제 upsert 대신 PriceCandle 직접 적재(dialect 우회, momentum 재계산 검증용).
    def _fake_upsert(db_, code, tf, candles):
        for c in candles:
            db_.execute(pg_insert(PC).values(
                stock_code=code, timeframe=tf, bar_date=c.ts.date(),
                open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            ).on_conflict_do_nothing())
        db_.commit()

    monkeypatch.setattr(ing.naver, "fetch_periodic_foreign", _fake_fetch)
    monkeypatch.setattr(ing.candle_service, "batch_upsert_periodic", _fake_upsert)

    res = ing.run_candle_backfill_progressive(db, workers=2)
    assert fetched == ["AAA.O"]  # BBB 는 이미 완료라 스킵
    assert res["done"] == 1 and res["remaining"] == 0
    # momentum 재계산: 64봉 100→130 = +30% → AAA 행에 반영.
    aaa = db.scalar(select(UsUniverse).where(UsUniverse.naver_symbol == "AAA.O"))
    assert aaa.momentum_3m == 30.0
    assert res["momentum_updated"] == 1


def test_candle_backfill_no_symbols(db):
    # 유니버스 스냅샷 없으면 빈 결과(안전).
    res = ing.run_candle_backfill_progressive(db)
    assert res == {"done": 0, "failed": 0, "remaining": 0, "momentum_updated": 0}
