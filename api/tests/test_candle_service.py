"""candle_service 단위 테스트 — DB 우선 조회 + 증분 갱신 로직(외부·DB 미접속).

세션과 chart 모듈을 스텁으로 대체해 '조회 시 외부를 안 탄다'·'배치 upsert'·'증분 구간'·
'중복 갱신 가드'를 검증한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.db.models import Timeframe
from app.services import candle_service


class _FakeScalars:
    def __init__(self, result):
        self._result = result

    def all(self):
        return self._result


class _FakeSession:
    """read_periodic/_latest_bar_date 만 흉내내는 최소 세션."""

    def __init__(self, rows=None, latest=None):
        self._rows = rows or []
        self._latest = latest
        self.executed = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalars(self._rows)

    def scalar(self, stmt):
        return self._latest

    def execute(self, stmt):
        self.executed.append(stmt)

    def commit(self):
        self.committed = True


def _candle(d: date):
    return candle_service.chart.Candle(
        ts=datetime(d.year, d.month, d.day), open=1, high=2, low=0, close=1.5, volume=100
    )


def test_is_stale_true_when_empty_or_old():
    assert candle_service.is_stale(_FakeSession(latest=None), "005930", "day") is True
    old = date.today() - timedelta(days=3)
    assert candle_service.is_stale(_FakeSession(latest=old), "005930", "day") is True


def test_is_stale_false_when_today():
    assert candle_service.is_stale(_FakeSession(latest=date.today()), "005930", "day") is False


def test_ensure_periodic_returns_db_without_fetch_when_present(monkeypatch):
    # DB 에 이미 있으면 외부 조회 함수를 절대 호출하지 않는다.
    called = {"fetch": 0}
    monkeypatch.setattr(
        candle_service.chart,
        "fetch_periodic_with_fallback",
        lambda *a, **k: called.__setitem__("fetch", called["fetch"] + 1) or [],
    )
    db = _FakeSession(rows=["r1", "r2"])
    out = candle_service.ensure_periodic(db, "005930", "day")
    assert out == ["r1", "r2"]
    assert called["fetch"] == 0  # 외부 미호출


def test_ensure_periodic_fetches_once_when_empty(monkeypatch):
    # DB 가 비면 최초 1회 동기 조회로 채운다.
    calls = {"fetch": 0}

    def _fetch(settings, code, tf, start, end, session):
        calls["fetch"] += 1
        return [_candle(date.today())]

    monkeypatch.setattr(candle_service.chart, "fetch_periodic_with_fallback", _fetch)
    # 첫 read 는 빈 리스트, upsert 후 read 도 스텁이라 빈 리스트지만 fetch 가 1회 불렸는지만 본다.
    db = _FakeSession(rows=[])
    candle_service.ensure_periodic(db, "999999", "day")
    assert calls["fetch"] == 1


def test_batch_upsert_single_statement(monkeypatch):
    # 493봉이어도 execute 는 1회(다중행 INSERT)여야 한다 — 개별 루프 회귀 방지.
    db = _FakeSession()
    n = candle_service.batch_upsert_periodic(
        db, "005930", Timeframe.DAY, [_candle(date(2026, 7, d)) for d in range(1, 11)]
    )
    assert n == 10
    assert len(db.executed) == 1  # 단일 배치
    assert db.committed is True


def test_batch_upsert_empty_noop():
    db = _FakeSession()
    assert candle_service.batch_upsert_periodic(db, "005930", Timeframe.DAY, []) == 0
    assert db.executed == []


def test_refresh_periodic_incremental_since_latest(monkeypatch):
    # 증분: 마지막 bar 이후만 조회(start 가 latest 하루 전 이후)여야 한다.
    captured = {}

    def _fetch(settings, code, tf, start, end, session):
        captured["start"] = start
        return []

    latest = date.today() - timedelta(days=2)
    monkeypatch.setattr(candle_service.chart, "fetch_periodic_with_fallback", _fetch)
    monkeypatch.setattr(candle_service, "SessionLocal", lambda: _FakeSession(latest=latest))

    candle_service.refresh_periodic("005930", "day")
    # start 는 전체 범위(2년 전)가 아니라 latest 근처여야 한다.
    assert captured["start"].date() >= latest - timedelta(days=2)


def test_refresh_periodic_dedups_inflight(monkeypatch):
    # 같은 (code, tf) 가 이미 갱신 중이면 두 번째 호출은 외부를 안 탄다.
    calls = {"fetch": 0}

    def _fetch(settings, code, tf, start, end, session):
        calls["fetch"] += 1
        return []

    monkeypatch.setattr(candle_service.chart, "fetch_periodic_with_fallback", _fetch)
    monkeypatch.setattr(candle_service, "SessionLocal", lambda: _FakeSession(latest=date.today()))

    candle_service._inflight.add("005930|day")  # 이미 진행 중으로 표시
    try:
        candle_service.refresh_periodic("005930", "day")
        assert calls["fetch"] == 0  # 가드로 건너뜀
    finally:
        candle_service._inflight.discard("005930|day")


@pytest.fixture(autouse=True)
def _clear_state():
    candle_service._inflight.clear()
    candle_service._last_attempt.clear()
    yield
    candle_service._inflight.clear()
    candle_service._last_attempt.clear()


def test_batch_upsert_dedups_same_date(monkeypatch):
    # 같은 bar_date 두 봉이 오면 하나로 합쳐 다중행 ON CONFLICT 21000 을 피한다.
    db = _FakeSession()
    dup = [_candle(date(2026, 7, 1)), _candle(date(2026, 7, 1)), _candle(date(2026, 7, 2))]
    n = candle_service.batch_upsert_periodic(db, "005930", Timeframe.DAY, dup)
    assert n == 2  # 날짜 2개로 축약
    assert len(db.executed) == 1


def test_refresh_periodic_cooldown_blocks_second(monkeypatch):
    # 쿨다운 내 재호출은 외부를 안 탄다(마감후·주말 헛된 반복 조회 방지).
    calls = {"fetch": 0}
    monkeypatch.setattr(
        candle_service.chart, "fetch_periodic_with_fallback",
        lambda *a, **k: calls.__setitem__("fetch", calls["fetch"] + 1) or [],
    )
    monkeypatch.setattr(candle_service, "SessionLocal", lambda: _FakeSession(latest=date.today()))
    candle_service.refresh_periodic("005930", "day")
    candle_service.refresh_periodic("005930", "day")  # 쿨다운 → 스킵
    assert calls["fetch"] == 1


def test_ensure_periodic_empty_code_cooldown(monkeypatch):
    # 데이터 없는 코드: 첫 요청만 동기 조회, 두 번째는 쿨다운으로 외부 미호출.
    calls = {"fetch": 0}
    monkeypatch.setattr(
        candle_service.chart, "fetch_periodic_with_fallback",
        lambda *a, **k: calls.__setitem__("fetch", calls["fetch"] + 1) or [],
    )
    candle_service.ensure_periodic(_FakeSession(rows=[]), "000000", "day")
    candle_service.ensure_periodic(_FakeSession(rows=[]), "000000", "day")
    assert calls["fetch"] == 1


def test_read_intraday_or_fetch_fills_when_empty(monkeypatch):
    # 30m DB 비면 최초 1회 조회로 채운다(첫 로드 빈 화면 회귀 방지).
    calls = {"fetch": 0}
    monkeypatch.setattr(
        candle_service.chart, "fetch_intraday_30min",
        lambda code, session: calls.__setitem__("fetch", calls["fetch"] + 1) or [],
    )
    monkeypatch.setattr(candle_service.intraday, "upsert_intraday", lambda *a: 0)
    candle_service.read_intraday_or_fetch(_FakeSession(rows=[]), "999999")
    assert calls["fetch"] == 1


def test_read_intraday_or_fetch_skips_when_present(monkeypatch):
    calls = {"fetch": 0}
    monkeypatch.setattr(
        candle_service.chart, "fetch_intraday_30min",
        lambda code, session: calls.__setitem__("fetch", calls["fetch"] + 1) or [],
    )
    out = candle_service.read_intraday_or_fetch(_FakeSession(rows=["a"]), "005930")
    assert out == ["a"]
    assert calls["fetch"] == 0
