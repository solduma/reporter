"""성장지표 계산 단위 테스트 — YoY·흑자전환·추정치 제외를 검증한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.services import growth


@dataclass
class _Fin:
    period: str
    revenue: float | None
    operating_income: float | None


def test_yoy_computed_against_same_quarter_prior_year():
    # 2026.03 vs 2025.03 (4분기 전 동분기)
    fins = [
        _Fin("2025.03", 100.0, 10.0),
        _Fin("2025.06", 110.0, 12.0),
        _Fin("2025.09", 120.0, 14.0),
        _Fin("2025.12", 130.0, 16.0),
        _Fin("2026.03", 150.0, 20.0),
    ]
    m = growth.compute_growth("A", fins)
    assert m.period == "2026.03"
    assert m.revenue_yoy == 0.5  # (150-100)/100
    assert m.op_yoy == 1.0  # (20-10)/10
    assert m.op_turnaround is False


def test_estimate_period_excluded_from_latest():
    # 추정치(E)는 기준분기가 되면 안 됨 → 최신 실적은 2026.03
    fins = [
        _Fin("2025.03", 100.0, 10.0),
        _Fin("2026.03", 150.0, 20.0),
        _Fin("2026.06(E)", 999.0, 999.0),
    ]
    m = growth.compute_growth("A", fins)
    assert m.period == "2026.03"
    assert m.revenue_yoy == 0.5


def test_turnaround_flag():
    # 직전 동기 영업적자 → 당기 흑자
    fins = [_Fin("2025.03", 100.0, -5.0), _Fin("2026.03", 120.0, 8.0)]
    m = growth.compute_growth("A", fins)
    assert m.op_turnaround is True
    assert m.op_yoy is None  # 직전이 음수라 비율은 None


def test_no_prior_year_yields_none_yoy():
    # 1년 전 동분기 데이터 없으면 YoY None
    fins = [_Fin("2026.03", 150.0, 20.0)]
    m = growth.compute_growth("A", fins)
    assert m.revenue_yoy is None
    assert m.op_yoy is None


def test_empty_or_all_estimate_returns_none():
    assert growth.compute_growth("A", []) is None
    assert growth.compute_growth("A", [_Fin("2026.06(E)", 1.0, 1.0)]) is None


def test_zero_prior_revenue_not_infinite():
    # 직전 매출 0 → 비율 왜곡 방지 None
    fins = [_Fin("2025.03", 0.0, 1.0), _Fin("2026.03", 100.0, 10.0)]
    m = growth.compute_growth("A", fins)
    assert m.revenue_yoy is None
