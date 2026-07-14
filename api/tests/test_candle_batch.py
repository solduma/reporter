"""저녁 봉 배치 단위 테스트 — 주식변동(종가 불일치) 감지 + 증분/재적재 분기.

DB·외부 조회를 스텁으로 대체해 판별·분기 로직만 검증한다.
"""

from __future__ import annotations

from datetime import date, datetime

from app.db.models import Timeframe
from app.services import candle_ingest


class _Stored:
    """PriceCandle 대역 — bar_date·close 만 쓴다."""

    def __init__(self, d: date, close: float):
        self.bar_date = d
        self.close = close


def _fresh(d: date, close: float) -> candle_ingest.chart.Candle:
    return candle_ingest.chart.Candle(
        ts=datetime(d.year, d.month, d.day), open=close, high=close, low=close, close=close, volume=1
    )


# ── _corporate_action ────────────────────────────────────────────────

def test_corporate_action_true_on_close_mismatch():
    # 직전 확정 bar(stored[-2]=7/8) 종가가 소급 조정됨(1000→500, 액면분할).
    stored = [_Stored(date(2026, 7, 8), 1000), _Stored(date(2026, 7, 9), 1010)]
    fresh = [_fresh(date(2026, 7, 8), 500), _fresh(date(2026, 7, 9), 505)]
    assert candle_ingest._corporate_action(stored, fresh) is True


def test_corporate_action_false_when_close_matches():
    stored = [_Stored(date(2026, 7, 8), 1000), _Stored(date(2026, 7, 9), 1010)]
    fresh = [_fresh(date(2026, 7, 8), 1000), _fresh(date(2026, 7, 9), 1010)]
    assert candle_ingest._corporate_action(stored, fresh) is False


def test_corporate_action_tolerates_float_noise():
    stored = [_Stored(date(2026, 7, 8), 1000.0), _Stored(date(2026, 7, 9), 1010.0)]
    fresh = [_fresh(date(2026, 7, 8), 1000.005), _fresh(date(2026, 7, 9), 1010.0)]
    assert candle_ingest._corporate_action(stored, fresh) is False  # eps 이내


def test_corporate_action_false_when_insufficient_history():
    stored = [_Stored(date(2026, 7, 9), 1010)]  # 1개뿐 → 대조 불가
    fresh = [_fresh(date(2026, 7, 9), 505)]
    assert candle_ingest._corporate_action(stored, fresh) is False


def test_corporate_action_false_when_ref_date_absent_in_fresh():
    # 새 조회에 직전 확정 bar 날짜가 없으면 판단 보류(증분).
    stored = [_Stored(date(2026, 7, 8), 1000), _Stored(date(2026, 7, 9), 1010)]
    fresh = [_fresh(date(2026, 7, 9), 1010)]  # 7/8 없음
    assert candle_ingest._corporate_action(stored, fresh) is False


# ── _seed_or_incremental 분기 + _detect_corporate_action ──────────

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, stored_desc):
        # _last_two_periodic 는 desc limit 2 후 reverse → 여기선 desc 리스트를 준다.
        self._stored_desc = stored_desc
        self.deleted = False
        self.commits = 0

    def scalars(self, stmt):
        return _FakeScalars(self._stored_desc[:2])

    def execute(self, stmt):
        # delete 문이면 파기 표시(간이 판별).
        if stmt.__class__.__name__ == "Delete":
            self.deleted = True

    def commit(self):
        self.commits += 1


def _settings():
    from app.config import Settings

    return Settings(ollama_api_key="k")


def test_seed_when_empty(monkeypatch):
    db = _FakeDB([])
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 9), 100)])
    upserts = {"n": 0}
    monkeypatch.setattr(candle_ingest, "_upsert", lambda *a: upserts.__setitem__("n", upserts["n"] + 1))
    out = candle_ingest._seed_or_incremental(db, _settings(), "005930", Timeframe.DAY, None)
    assert out == "seed"
    assert upserts["n"] == 1


def test_incremental_when_stored_present(monkeypatch):
    stored_desc = [_Stored(date(2026, 7, 9), 1010), _Stored(date(2026, 7, 8), 1000)]
    db = _FakeDB(stored_desc)
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 8), 1000), _fresh(date(2026, 7, 9), 1010)])
    monkeypatch.setattr(candle_ingest, "_upsert", lambda *a: None)
    out = candle_ingest._seed_or_incremental(db, _settings(), "005930", Timeframe.DAY, None)
    assert out == "incremental"
    assert db.deleted is False  # seed/incremental 은 삭제하지 않음


def test_detect_corporate_action_true_on_mismatch(monkeypatch):
    # 일봉 직전 확정 bar(7/8) 종가가 소급 조정(1000→500) → 주식변동 감지.
    stored_desc = [_Stored(date(2026, 7, 9), 1010), _Stored(date(2026, 7, 8), 1000)]
    db = _FakeDB(stored_desc)
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 8), 500), _fresh(date(2026, 7, 9), 505)])
    assert candle_ingest._detect_corporate_action(db, _settings(), "005930", None) is True


def test_detect_corporate_action_false_on_match(monkeypatch):
    stored_desc = [_Stored(date(2026, 7, 9), 1010), _Stored(date(2026, 7, 8), 1000)]
    db = _FakeDB(stored_desc)
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 8), 1000), _fresh(date(2026, 7, 9), 1010)])
    assert candle_ingest._detect_corporate_action(db, _settings(), "005930", None) is False


def test_reload_stock_deletes_then_refills(monkeypatch):
    db = _FakeDB([])
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 9), 100)])
    monkeypatch.setattr(candle_ingest.chart, "fetch_intraday_30min", lambda *a: [])
    monkeypatch.setattr(candle_ingest, "_upsert", lambda *a: None)
    candle_ingest._reload_stock(db, _settings(), "005930", None)
    assert db.deleted is True  # 전체 파기 후 재적재


# ── 장중 일봉 증분 갱신(refresh_today_day_candles) ──────────────────────

def test_refresh_today_day_candles_upserts_all(monkeypatch):
    codes = ["A", "B", "C"]
    monkeypatch.setattr(candle_ingest, "_universe_codes", lambda db: codes)
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback",
                        lambda *a, **k: [_fresh(date(2026, 7, 14), 100)])
    upserted: list[str] = []
    monkeypatch.setattr(candle_ingest.candle_service, "batch_upsert_periodic",
                        lambda db, code, tf, candles: upserted.append(code))
    out = candle_ingest.refresh_today_day_candles(db=object(), settings=_settings())
    assert out == {"updated": 3, "failed": 0, "total": 3}
    assert sorted(upserted) == codes  # 전 종목 오늘 봉 기록


def test_refresh_today_day_candles_skips_empty_and_survives_failure(monkeypatch):
    codes = ["OK", "EMPTY", "BOOM"]

    def _fetch(settings, code, tf, start, end, session):
        if code == "EMPTY":
            return []
        if code == "BOOM":
            raise RuntimeError("naver throttled")
        return [_fresh(date(2026, 7, 14), 100)]

    monkeypatch.setattr(candle_ingest, "_universe_codes", lambda db: codes)
    monkeypatch.setattr(candle_ingest.chart, "fetch_periodic_with_fallback", _fetch)
    upserted: list[str] = []

    class _DB:
        def rollback(self):
            pass

    monkeypatch.setattr(candle_ingest.candle_service, "batch_upsert_periodic",
                        lambda db, code, tf, candles: upserted.append(code))
    out = candle_ingest.refresh_today_day_candles(db=_DB(), settings=_settings())
    # OK 만 upsert(EMPTY 는 빈 응답 skip), BOOM 은 실패로 집계되나 사이클은 계속.
    assert upserted == ["OK"]
    assert out == {"updated": 1, "failed": 1, "total": 3}


def test_refresh_today_day_candles_no_universe(monkeypatch):
    monkeypatch.setattr(candle_ingest, "_universe_codes", lambda db: [])
    out = candle_ingest.refresh_today_day_candles(db=object(), settings=_settings())
    assert out == {"updated": 0, "failed": 0, "total": 0}
