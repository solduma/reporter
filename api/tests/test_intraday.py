"""30분봉 누적 서비스 단위 테스트 — 추적 종목 선정·누적 흐름을 목킹으로 검증한다."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from app.adapters.market import naver as chart
from app.services import intraday


def test_tracked_stock_codes_filters_none():
    db = MagicMock()
    db.scalars.return_value.all.return_value = ["005930", None, "000660"]
    assert intraday.tracked_stock_codes(db) == ["005930", "000660"]


def test_accumulate_intraday_upserts_per_code(monkeypatch):
    monkeypatch.setattr(intraday, "tracked_stock_codes", lambda db: ["005930", "000660"])
    candle = chart.Candle(ts=datetime(2026, 7, 8, 9, 0), open=1, high=2, low=1, close=2, volume=10)
    monkeypatch.setattr(chart, "fetch_intraday_30min", lambda code, session: [candle])
    upserts: list[str] = []
    monkeypatch.setattr(intraday, "upsert_intraday", lambda db, code, candles: upserts.append(code))

    db = MagicMock()
    touched = intraday.accumulate_intraday(db)

    assert touched == 2
    assert upserts == ["005930", "000660"]


def test_accumulate_intraday_skips_empty_and_isolates_failure(monkeypatch):
    monkeypatch.setattr(intraday, "tracked_stock_codes", lambda db: ["A", "B", "C"])

    def _fetch(code, session):
        if code == "A":
            return []  # 데이터 없음 → skip
        if code == "B":
            raise RuntimeError("network")  # 실패 → 격리, 배치 계속
        return [chart.Candle(ts=datetime(2026, 7, 8, 9, 0), open=1, high=2, low=1, close=2, volume=1)]

    monkeypatch.setattr(chart, "fetch_intraday_30min", _fetch)
    upserts: list[str] = []
    monkeypatch.setattr(intraday, "upsert_intraday", lambda db, code, candles: upserts.append(code))

    db = MagicMock()
    touched = intraday.accumulate_intraday(db)

    assert touched == 1  # C 만 성공
    assert upserts == ["C"]
