"""포트 치환성(P1) 테스트 — candle_service·universe_ingest 의 공급자 seam 을 fake 포트로
교체해, DB·네트워크 없이 응용 로직이 포트 인터페이스만으로 돈다는 것을 실증한다.

seam(_candle_repo·_market_data·_universe_repo)이 진짜 substitutable 하지 않으면(하드코딩이면)
이 테스트는 성립하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime

from app.services import candle_service, universe_ingest


class _FakeCandleRepo:
    """CandleRepository 포트를 만족하는 인메모리 fake — Session·SQLAlchemy 전혀 없음."""

    def __init__(self):
        self.upserts: list = []
        self._periodic = [object(), object()]

    def read_periodic(self, code, tf):
        return self._periodic

    def read_intraday(self, code, days=14):
        return []

    def latest_bar_date(self, code, tf):
        return date(2026, 7, 10)

    def upsert_periodic(self, code, tf, candles):
        self.upserts.append((code, tf, list(candles)))
        return len(candles)


class _FakeMarketData:
    """MarketDataPort 를 만족하는 fake — 정해둔 봉만 돌려준다."""

    def __init__(self, bars):
        self._bars = bars
        self.periodic_calls = 0

    def fetch_periodic(self, code, timeframe, start, end):
        self.periodic_calls += 1
        return self._bars

    def fetch_intraday_30min(self, code):
        return []


def test_read_periodic_uses_injected_repo(monkeypatch):
    repo = _FakeCandleRepo()
    monkeypatch.setattr(candle_service, "_candle_repo", lambda db: repo)
    # db 자리에 아무 객체나 줘도 된다 — 포트가 세션을 감추므로 실제로 안 쓰인다.
    out = candle_service.read_periodic(object(), "005930", "day")
    assert out == repo._periodic  # DB 없이 fake 포트가 응답


def test_fetch_and_store_routes_market_and_repo_ports(monkeypatch):
    bar = candle_service.chart.Candle(
        ts=datetime(2026, 7, 10), open=1, high=2, low=0, close=1.5, volume=100
    )
    repo = _FakeCandleRepo()
    market = _FakeMarketData([bar])
    monkeypatch.setattr(candle_service, "_candle_repo", lambda db: repo)
    monkeypatch.setattr(candle_service, "_market_data", lambda m: market)

    n = candle_service._fetch_and_store(object(), "005930", "day", since=None, market="KR")

    assert market.periodic_calls == 1  # 시세는 MarketDataPort 로
    assert n == 1 and len(repo.upserts) == 1  # 영속화는 CandleRepository 로


def test_latest_snapshot_date_uses_injected_repo(monkeypatch):
    class _FakeUniverseRepo:
        def latest_snapshot_date(self):
            return date(2026, 7, 12)

    monkeypatch.setattr(universe_ingest, "_universe_repo", lambda db: _FakeUniverseRepo())
    assert universe_ingest.latest_snapshot_date(object()) == date(2026, 7, 12)
