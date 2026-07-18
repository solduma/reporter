"""trend 서비스 오케스트레이션 테스트 — candle_service 를 스텁해 봉 로드를 대체한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services import trend


@dataclass
class _Bar:
    bar_date: date
    close: float
    volume: int = 1000
    high: float = 0.0
    low: float = 0.0


def _series(n: int, fn) -> list[_Bar]:
    d0 = date(2024, 1, 1)
    return [
        _Bar(bar_date=d0 + timedelta(days=i), close=fn(i), high=fn(i) * 1.01, low=fn(i) * 0.99)
        for i in range(n)
    ]


def test_compute_trend_uses_market_benchmark(monkeypatch):
    # 종목=상승, 지수=완만 → 아웃퍼폼 + 중기 국면 ②, 벤치마크는 시장(KOSDAQ)으로 선택.
    calls: list[str] = []

    def _ensure(db, code, tf, market="KR"):
        calls.append(code)
        if code == "KOSDAQ":
            return _series(260, lambda i: 100.0 * (1.002**i))
        return _series(260, lambda i: 100.0 * (1.01**i))

    monkeypatch.setattr(trend.candle_service, "ensure_periodic", _ensure)

    result = trend.compute_trend(db=None, code="123456", market="KOSDAQ")
    assert result.benchmark == "KOSDAQ"
    assert "KOSDAQ" in calls  # 벤치마크 지수 봉을 로드했다
    assert result.stages["mid"].stage == 2
    assert result.rs.outperforming is True
    assert result.stage_segments  # 국면 구간이 만들어진다


def test_compute_trend_defaults_benchmark_when_market_unknown(monkeypatch):
    monkeypatch.setattr(
        trend.candle_service,
        "ensure_periodic",
        lambda db, code, tf, market="KR": _series(260, lambda i: 100.0 + i),
    )
    result = trend.compute_trend(db=None, code="123456", market=None)
    assert result.benchmark == "KOSPI"  # 시장 미상 → 기본 KOSPI


# ── 사전계산 캐시 왕복 ────────────────────────────────────────────────────
import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.models import Base, PriceCandle, Timeframe, TrendCache, UniverseSnapshot  # noqa: E402


# SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON 으로 렌더해 create_all 통과.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def cache_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            TrendCache.__table__, PriceCandle.__table__, UniverseSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _day_candle(code, d, close=100.0):
    return PriceCandle(
        stock_code=code, timeframe=Timeframe.DAY, bar_date=d,
        open=close, high=close, low=close, close=close, volume=1000,
    )


def test_store_and_get_cached_trend_roundtrip(cache_db, monkeypatch):
    # 봉·스냅샷 세팅 후 store → get 이 payload 를 복원하고 rs_rating 을 스냅샷에서 주입한다.
    monkeypatch.setattr(
        trend.candle_service, "ensure_periodic",
        lambda db, code, tf, market="KR": _series(260, lambda i: 100.0 + i),
    )
    cache_db.add(_day_candle("123456", date(2026, 7, 16)))
    cache_db.add(
        UniverseSnapshot(
            snapshot_date=date(2026, 7, 16), stock_code="123456", market="KOSPI",
            stock_name="X", stock_type="stock", market_cap=1.0, close_price=100.0, rs_rating=88,
        )
    )
    cache_db.commit()

    result = trend.compute_trend(db=cache_db, code="123456", market="KOSPI")
    as_of = trend.store_trend(cache_db, "123456", result)
    assert as_of == date(2026, 7, 16)

    cached = trend.get_cached_trend(cache_db, "123456")
    assert cached is not None
    assert cached.rs_rating == 88  # 스냅샷에서 주입(페이로드엔 없음)
    assert len(cached.stages) == 3


def test_get_cached_trend_miss_when_no_row(cache_db):
    cache_db.add(_day_candle("123456", date(2026, 7, 16)))
    cache_db.commit()
    assert trend.get_cached_trend(cache_db, "123456") is None


def test_get_cached_trend_stale_when_newer_candle(cache_db, monkeypatch):
    # 캐시 as_of 보다 최신 확정봉이 생기면 stale(None) → 재계산 유도.
    monkeypatch.setattr(
        trend.candle_service, "ensure_periodic",
        lambda db, code, tf, market="KR": _series(260, lambda i: 100.0 + i),
    )
    cache_db.add(_day_candle("123456", date(2026, 7, 16)))
    cache_db.commit()
    trend.store_trend(cache_db, "123456", trend.compute_trend(cache_db, "123456", "KOSPI"))
    assert trend.get_cached_trend(cache_db, "123456") is not None
    # 다음날 확정봉 추가 → 기존 캐시는 stale
    cache_db.add(_day_candle("123456", date(2026, 7, 17)))
    cache_db.commit()
    assert trend.get_cached_trend(cache_db, "123456") is None
