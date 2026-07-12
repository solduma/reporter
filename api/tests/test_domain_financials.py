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


def test_discrete_then_sum_recovers_annual():
    # 분기 개별값(1~3Q 그대로 + Q4=연간-누적)의 합은 연간값과 같아야 한다(이중계상 방지).
    raw = {(2024, 1): 72.0, (2024, 2): 74.0, (2024, 3): 79.0, (2024, 4): 300.0}
    discrete = [f.discrete_quarter(raw, (2024, q)) for q in (1, 2, 3, 4)]
    assert sum(discrete) == 300.0  # 72+74+79+75
