"""차트 서비스 단위 테스트 — 30분 리샘플·주기봉 파싱을 목킹된 응답으로 검증한다."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.market import naver as chart


def _session_returning(payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def _minute(dt: str, o, h, low, cur, vol):
    return {
        "localDateTime": dt,
        "openPrice": o,
        "highPrice": h,
        "lowPrice": low,
        "currentPrice": cur,
        "accumulatedTradingVolume": vol,
    }


def test_resample_30min_buckets_and_aggregates():
    # 09:00~09:29 한 버킷: OHLC=first open/max high/min low/last close, 볼륨 합
    rows = [
        _minute("20260708090000", 100, 110, 95, 105, 10),
        _minute("20260708091500", 105, 120, 100, 118, 20),
        _minute("20260708092900", 118, 119, 90, 92, 5),
        _minute("20260708093000", 92, 93, 91, 93, 7),  # 다음 버킷(09:30)
    ]
    candles = chart._resample_30min(rows)
    assert len(candles) == 2
    first = candles[0]
    assert first.ts.strftime("%H:%M") == "09:00"
    assert first.open == 100  # 첫 분봉 시가
    assert first.high == 120  # 구간 최고
    assert first.low == 90    # 구간 최저
    assert first.close == 92  # 마지막 분봉 currentPrice
    assert first.volume == 35  # 10+20+5
    assert candles[1].ts.strftime("%H:%M") == "09:30"
    assert candles[1].open == 92


def test_resample_sorts_bucket_for_correct_open_close():
    # 입력이 뒤섞여 와도 버킷 내부를 시각순 정렬해 open=첫 시각, close=마지막 시각이어야 한다
    rows = [
        _minute("20260708091500", 105, 120, 100, 118, 20),
        _minute("20260708090000", 100, 110, 95, 105, 10),  # 실제로는 더 이른 시각
        _minute("20260708092900", 118, 119, 90, 92, 5),
    ]
    candles = chart._resample_30min(rows)
    assert len(candles) == 1
    assert candles[0].open == 100  # 09:00 시가
    assert candles[0].close == 92  # 09:29 종가


def test_resample_skips_malformed_rows():
    rows = [
        _minute("20260708090000", 100, 110, 95, 105, 10),
        {"localDateTime": "bad", "openPrice": 1},  # 파싱 불가 → 스킵
    ]
    candles = chart._resample_30min(rows)
    assert len(candles) == 1


def test_fetch_periodic_parses_daily():
    payload = [
        {
            "localDate": "20260708",
            "openPrice": 285500.0,
            "highPrice": 300000.0,
            "lowPrice": 273500.0,
            "closePrice": 277000.0,
            "accumulatedTradingVolume": 27768050,
            "foreignRetentionRate": 46.5,
        }
    ]
    from datetime import datetime

    candles = chart.fetch_periodic(
        "005930", "day", datetime(2026, 7, 1), datetime(2026, 7, 8), _session_returning(payload)
    )
    assert len(candles) == 1
    c = candles[0]
    assert c.ts.date().isoformat() == "2026-07-08"
    assert (c.open, c.high, c.low, c.close) == (285500.0, 300000.0, 273500.0, 277000.0)
    assert c.foreign_ratio == 46.5


def test_fetch_periodic_handles_non_list_response():
    candles = chart.fetch_periodic(
        "005930", "month", __import__("datetime").datetime(2023, 1, 1),
        __import__("datetime").datetime(2026, 1, 1), _session_returning({"error": "x"})
    )
    assert candles == []


def test_fetch_periodic_foreign_parses_us_etf():
    # 미국 foreign 응답은 domestic 과 스키마 동일하되 외국인비율 필드가 없다 → None.
    from datetime import datetime

    payload = [
        {
            "localDate": "20260708",
            "openPrice": 177.48,
            "highPrice": 181.63,
            "lowPrice": 177.15,
            "closePrice": 181.4,
            "accumulatedTradingVolume": 10528340,
        }
    ]
    candles = chart.fetch_periodic_foreign(
        "XLK", "day", datetime(2026, 7, 1), datetime(2026, 7, 8), _session_returning(payload)
    )
    assert len(candles) == 1
    c = candles[0]
    assert c.close == 181.4 and c.volume == 10528340
    assert c.foreign_ratio is None  # 미국은 외국인 수급 없음


def test_fetch_periodic_foreign_uses_foreign_endpoint():
    from datetime import datetime

    session = _session_returning([])
    chart.fetch_periodic_foreign(
        "SMH.O", "day", datetime(2026, 7, 1), datetime(2026, 7, 8), session
    )
    called_url = session.get.call_args.args[0]
    assert "chart/foreign/item/SMH.O/day" in called_url
