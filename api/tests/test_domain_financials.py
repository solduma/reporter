"""KR 재무 분기환산·TTM 도메인 규칙 단위 테스트(DART 회계 관례).

실측 확정: DART fnlttSinglAcntAll thstrm_amount 는 1~3Q 당기 3개월 개별값, 4Q 연간 누적.
"""

from __future__ import annotations

from app.domain import financials as f


def test_prev_yq():
    assert f.prev_yq((2026, 1)) == (2025, 4)  # Q1 이전은 전년 Q4
    assert f.prev_yq((2026, 3)) == (2026, 2)


def test_discrete_1to3q_passthrough():
    # 1~3Q 는 이미 개별값이므로 그대로(차감 없음) — 옛 YTD 가정의 핵심 버그.
    raw = {(2025, 1): 72.0, (2025, 2): 74.0, (2025, 3): 79.0}
    assert f.discrete_quarter(raw, (2025, 1)) == 72.0
    assert f.discrete_quarter(raw, (2025, 2)) == 74.0
    assert f.discrete_quarter(raw, (2025, 3)) == 79.0


def test_discrete_q4_is_annual_minus_first_three():
    # Q4 = 연간(사업보고서) - (Q1+Q2+Q3). 삼성 2024 유사: 72+74+79 개별, 연간 300 → Q4 75.
    raw = {(2024, 1): 72.0, (2024, 2): 74.0, (2024, 3): 79.0, (2024, 4): 300.0}
    assert f.discrete_quarter(raw, (2024, 4)) == 75.0


def test_discrete_q4_none_when_missing_prior():
    raw = {(2024, 2): 74.0, (2024, 4): 300.0}  # Q1·Q3 없음
    assert f.discrete_quarter(raw, (2024, 4)) is None


def test_ttm_sums_four_discrete_quarters():
    # TTM(Q4 기준) = Q1+Q2+Q3 개별 + Q4개별 = 연간값.
    raw = {(2024, 1): 72.0, (2024, 2): 74.0, (2024, 3): 79.0, (2024, 4): 300.0}
    assert f.ttm(raw, (2024, 4)) == 300.0  # 72+74+79+75


def test_ttm_crosses_year_boundary():
    # 2025.Q2 기준 TTM = 2025Q2 + 2025Q1 + 2024Q4개별 + 2024Q3.
    raw = {
        (2024, 1): 10.0, (2024, 2): 10.0, (2024, 3): 10.0, (2024, 4): 60.0,  # Q4개별=30
        (2025, 1): 20.0, (2025, 2): 25.0,
    }
    # TTM(2025,2) = 25 + 20 + 30(2024Q4개별) + 10(2024Q3) = 85
    assert f.ttm(raw, (2025, 2)) == 85.0


def test_ttm_none_on_gap():
    raw = {(2025, 1): 100.0, (2025, 3): 170.0, (2025, 4): 600.0}  # Q2 누락
    assert f.ttm(raw, (2025, 4)) is None
