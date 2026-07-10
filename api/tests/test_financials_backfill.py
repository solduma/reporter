"""재무 10년 백필 단위 테스트 — 분기환산(4Q=연간-누적)·TTM·분할무관 밸류 계산."""

from __future__ import annotations

from app.services import financials_backfill as fb


def test_discrete_q1_to_q3_passthrough_q4_subtracts():
    # 1~3Q 는 당기값 그대로, 4Q=연간-(1Q+2Q+3Q).
    cum = {(2023, 1): 10.0, (2023, 2): 20.0, (2023, 3): 30.0, (2023, 4): 100.0}
    assert fb._discrete(cum, (2023, 1)) == 10.0
    assert fb._discrete(cum, (2023, 2)) == 20.0
    assert fb._discrete(cum, (2023, 3)) == 30.0
    assert fb._discrete(cum, (2023, 4)) == 40.0  # 100-(10+20+30)


def test_discrete_q4_missing_part_returns_none():
    # 4Q 환산에 1~3Q 중 하나라도 없으면 None(15개월 오인 방지).
    cum = {(2023, 1): 10.0, (2023, 3): 30.0, (2023, 4): 100.0}  # 2Q 결측
    assert fb._discrete(cum, (2023, 4)) is None


def test_ttm_sums_four_consecutive_quarters():
    # yq 포함 연속 4개 분기 개별값 합.
    discrete = {(2023, 1): 1.0, (2023, 2): 2.0, (2023, 3): 3.0, (2023, 4): 4.0}
    assert fb._ttm(discrete, (2023, 4)) == 10.0
    # 하나라도 결측이면 None.
    assert fb._ttm(discrete, (2023, 3)) is None  # 2022 4Q 없음


def test_ttm_crosses_year_boundary():
    discrete = {(2022, 4): 4.0, (2023, 1): 1.0, (2023, 2): 2.0, (2023, 3): 3.0}
    assert fb._ttm(discrete, (2023, 3)) == 10.0  # 23Q3+23Q2+23Q1+22Q4


def test_period_str_maps_quarter_to_month():
    assert fb._period_str(2026, 1) == "2026.03"
    assert fb._period_str(2026, 4) == "2026.12"


def test_target_year_quarters_excludes_future():
    from datetime import date

    yqs = fb._target_year_quarters(date(2026, 7, 10))
    # 2026 3Q(9월말)·4Q 는 미래라 제외, 2026 2Q(6월말)까지 포함.
    assert (2026, 2) in yqs
    assert (2026, 3) not in yqs
    assert (2026, 4) not in yqs
    # 10년 전 시작.
    assert yqs[0][0] == 2016
