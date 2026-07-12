"""Mansfield 상대강도(RS) 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import relative_strength as rs


def _dates(n: int) -> list[str]:
    return [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def test_outperforming_stock_has_positive_latest():
    # 종목이 지수보다 빠르게 오르면 RP 가 자기 이평 위 → MRP>0(아웃퍼폼).
    dates = _dates(260)
    stock = [(d, 100.0 * (1.01**i)) for i, d in enumerate(dates)]  # +1%/일
    bench = [(d, 100.0 * (1.002**i)) for i, d in enumerate(dates)]  # +0.2%/일
    out = rs.compute(stock, bench, period=200)
    assert out.latest is not None and out.latest > 0
    assert out.outperforming is True
    assert len(out.series) == 260 - 200 + 1


def test_underperforming_stock_has_negative_latest():
    dates = _dates(260)
    stock = [(d, 100.0 * (0.99**i)) for i, d in enumerate(dates)]  # 하락
    bench = [(d, 100.0 * (1.002**i)) for i, d in enumerate(dates)]
    out = rs.compute(stock, bench, period=200)
    assert out.latest is not None and out.latest < 0
    assert out.outperforming is False


def test_date_intersection_only():
    # 지수에 없는 날짜는 제외하고 공통일로만 계산한다.
    dates = _dates(260)
    stock = [(d, 100.0 + i) for i, d in enumerate(dates)]
    bench = [(d, 100.0 + i * 0.5) for i, d in enumerate(dates) if i % 2 == 0]  # 절반만
    out = rs.compute(stock, bench, period=50)
    # 공통일이 130개(짝수 인덱스) → period=50 이면 시리즈 존재.
    assert out.series
    assert all(p.date in {d for d, _ in bench} for p in out.series)


def test_insufficient_common_days_returns_empty():
    dates = _dates(100)
    stock = [(d, 100.0 + i) for i, d in enumerate(dates)]
    bench = [(d, 100.0) for d in dates]
    out = rs.compute(stock, bench, period=200)  # 공통일 100 < 200
    assert out.series == []
    assert out.latest is None
    assert out.outperforming is None
