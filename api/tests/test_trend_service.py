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
