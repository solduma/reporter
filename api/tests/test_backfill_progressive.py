"""10년 점진 백필 단위 테스트 — 미완 종목만 per_run 개, 재개 가능(완료 마킹)."""

from __future__ import annotations

from datetime import date, datetime

from app.services import candle_ingest


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """_universe_codes·_backfilled_codes·mark 를 흉내내는 최소 세션."""

    def __init__(self, universe, already):
        self._universe = universe
        self._already = set(already)
        self.marked: list[str] = []
        self.commits = 0

    def scalar(self, stmt):
        return date(2026, 7, 10)  # _universe_codes 의 max(snapshot_date)

    def scalars(self, stmt):
        # 첫 호출은 universe, 그 다음은 backfilled set — 쿼리 구분이 어려워 호출 순서로.
        # 실제로는 candle_ingest 가 _universe_codes → _backfilled_codes 순 호출.
        if not hasattr(self, "_calls"):
            self._calls = 0
        self._calls += 1
        return _FakeScalars(self._universe if self._calls == 1 else list(self._already))

    def execute(self, stmt):
        pass

    def commit(self):
        self.commits += 1


def _fresh(d: date):
    return candle_ingest.chart.Candle(
        ts=datetime(d.year, d.month, d.day), open=1, high=1, low=1, close=1, volume=1
    )


def _settings():
    from app.config import Settings

    return Settings(ollama_api_key="k")


def test_backfill_processes_only_pending_up_to_per_run(monkeypatch):
    db = _FakeDB(universe=["A", "B", "C", "D"], already={"A"})  # A 이미 완료
    fetched = {"codes": []}

    def _fetch(settings, code, tf, start, end, session):
        fetched["codes"].append(code)
        return [_fresh(date(2020, 1, 2))]

    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback", _fetch)
    monkeypatch.setattr(candle_ingest, "_upsert", lambda *a: None)
    marked = []
    monkeypatch.setattr(candle_ingest.sync_state, "mark", lambda db, dom, code: marked.append(code))
    monkeypatch.setattr(candle_ingest.time, "sleep", lambda s: None)

    out = candle_ingest.run_backfill_progressive(db, _settings(), per_run=2)

    # A 는 스킵, B·C 만 처리(per_run=2), D 는 다음 밤. pending=3(B,C,D), done=2 → remaining 1.
    assert fetched["codes"] == ["B", "C"]
    assert marked == ["B", "C"]
    assert out == {"done": 2, "failed": 0, "remaining": 1}


def test_backfill_marks_even_without_candles(monkeypatch):
    # 봉이 없는(신규상장 등) 종목도 완료 마킹해 매일 재시도하지 않게 한다.
    db = _FakeDB(universe=["X"], already=set())
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback", lambda *a, **k: [])
    monkeypatch.setattr(candle_ingest, "_upsert", lambda *a: None)
    marked = []
    monkeypatch.setattr(candle_ingest.sync_state, "mark", lambda db, dom, code: marked.append(code))
    monkeypatch.setattr(candle_ingest.time, "sleep", lambda s: None)

    out = candle_ingest.run_backfill_progressive(db, _settings(), per_run=10)
    assert marked == ["X"]
    assert out["done"] == 1


def test_backfill_uses_10y_range():
    # _DAY_RANGE_DAYS 가 10년(3600+일)인지 회귀 방지.
    assert candle_ingest._DAY_RANGE_DAYS >= 365 * 10
