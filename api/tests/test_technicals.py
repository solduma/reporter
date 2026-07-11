"""기술적 지표(technicals) 순수 계산 단위 테스트."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import technicals


@dataclass
class _Bar:
    close: float
    high: float
    low: float
    volume: int


def _series(closes: list[float], vol: int = 1000) -> list[_Bar]:
    return [_Bar(close=c, high=c * 1.01, low=c * 0.99, volume=vol) for c in closes]


def test_empty_returns_all_none():
    t = technicals.compute([])
    assert t.trend_score is None and t.last_close is None


def test_uptrend_is_aligned_and_near_high():
    # 130일 꾸준한 상승 → 정배열 + 신고가 근접 + 양의 3개월 수익률.
    bars = _series([100 + i for i in range(130)])
    t = technicals.compute(bars)
    assert t.ma_aligned is True
    assert t.above_ma120 is True
    assert t.near_high_pct is not None and t.near_high_pct >= 99  # 마지막이 사실상 신고가
    assert t.return_3m is not None and t.return_3m > 0
    assert t.trend_score is not None and t.trend_score > 60


def test_downtrend_not_aligned():
    bars = _series([300 - i for i in range(130)])
    t = technicals.compute(bars)
    assert t.ma_aligned is False
    assert t.above_ma120 is False
    assert t.near_high_pct is not None and t.near_high_pct < 60  # 고점 대비 크게 하락
    assert t.trend_score is not None and t.trend_score < 40


def test_short_series_leaves_ma_none():
    t = technicals.compute(_series([100, 101, 102]))
    assert t.ma120 is None and t.ma_aligned is None
    assert t.last_close == 102


def test_volume_spike_detected():
    bars = _series([100 + i for i in range(60)], vol=1000)
    bars[-1].volume = 5000  # 마지막 날 거래량 급증
    t = technicals.compute(bars)
    assert t.vol_ratio is not None and t.vol_ratio > 3
