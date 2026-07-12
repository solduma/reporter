"""IBD RS Rating 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import rs_rating


def test_strength_factor_positive_for_uptrend():
    # 꾸준한 상승 → 모든 ROC 양수 → 강도지수 양수.
    closes = [100.0 * (1.002**i) for i in range(300)]
    sf = rs_rating.strength_factor(closes)
    assert sf is not None and sf > 0


def test_strength_factor_negative_for_downtrend():
    closes = [300.0 * (0.998**i) for i in range(300)]
    sf = rs_rating.strength_factor(closes)
    assert sf is not None and sf < 0


def test_strength_factor_weights_recent_quarter_double():
    # 최근 63일만 급등하고 이전은 평탄하면, 0.4 가중된 최근 ROC 가 강도지수를 끌어올린다.
    flat = [100.0] * 240
    surge = [100.0 * (1.01**i) for i in range(1, 61)]
    sf = rs_rating.strength_factor(flat + surge)
    assert sf is not None and sf > 0


def test_strength_factor_insufficient_data():
    assert rs_rating.strength_factor([100.0] * 200) is None  # 252+1 미만
    assert rs_rating.strength_factor([]) is None


def test_to_rating_range_and_ordering():
    factors = [i * 0.01 for i in range(100)]  # 0.00 ~ 0.99
    sorted_f = sorted(factors)
    # 최상위·최하위·중앙값 rating 이 1~99 범위, 순서 보존.
    top = rs_rating.to_rating(0.99, sorted_f)
    bottom = rs_rating.to_rating(0.00, sorted_f)
    mid = rs_rating.to_rating(0.50, sorted_f)
    assert top == 99
    assert bottom == 1
    assert bottom < mid < top
    assert all(1 <= rs_rating.to_rating(f, sorted_f) <= 99 for f in factors)


def test_to_rating_none_when_no_factor_or_small_sample():
    assert rs_rating.to_rating(None, [0.1, 0.2]) is None
    assert rs_rating.to_rating(0.1, [0.1]) is None  # 표본 1개
