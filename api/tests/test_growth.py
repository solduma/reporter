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
    assert m.op_status == "흑자전환"
    assert m.op_yoy is None  # 직전이 음수라 비율은 None
    # 흑자전환 규모 = Δ영업이익률: 8/120 - (-5/100) = 0.0667 + 0.05 = 0.1167
    assert m.op_margin_delta == 0.1167


def test_margin_delta_normalizes_by_company_size():
    # 같은 절대 흑자(+8)라도 매출 규모가 작을수록 이익률 개선폭(규모)이 크다.
    small = growth.compute_growth("S", [_Fin("2025.03", 50.0, -5.0), _Fin("2026.03", 50.0, 8.0)])
    large = growth.compute_growth("L", [_Fin("2025.03", 500.0, -5.0), _Fin("2026.03", 500.0, 8.0)])
    assert small.op_margin_delta > large.op_margin_delta  # 회사 규모 대비 흑전 폭 반영


def test_margin_delta_none_when_revenue_nonpositive():
    # 매출 0/음수/결측이면 이익률 정의 불가 → None(YoY 처럼 왜곡 방지).
    m = growth.compute_growth("A", [_Fin("2025.03", 0.0, -5.0), _Fin("2026.03", 100.0, 8.0)])
    assert m.op_margin_delta is None


def test_op_status_four_states():
    # 4가지 손익 상태가 정확히 구분되어야 한다(이진 흑자전환이 나머지 셋을 뭉개던 문제).
    def status(prior_op, latest_op):
        m = growth.compute_growth("A", [_Fin("2025.03", 100.0, prior_op), _Fin("2026.03", 100.0, latest_op)])
        return m.op_status, m.op_turnaround

    assert status(-5.0, 8.0) == ("흑자전환", True)  # 적자→흑자
    assert status(10.0, 20.0) == ("흑자지속", False)  # 흑자→흑자
    assert status(10.0, -3.0) == ("적자전환", False)  # 흑자→적자
    assert status(-5.0, -2.0) == ("적자지속", False)  # 적자→적자


def test_op_status_none_without_prior():
    # 1년 전 동분기가 없으면 손익 상태 판단 불가.
    m = growth.compute_growth("A", [_Fin("2026.03", 150.0, 20.0)])
    assert m.op_status is None
    assert m.op_turnaround is False


def test_op_status_zero_prior_treated_as_loss():
    # 직전 영업이익 0 은 비흑자로 본다 → 당기 흑자면 흑자전환.
    m = growth.compute_growth("A", [_Fin("2025.03", 100.0, 0.0), _Fin("2026.03", 100.0, 5.0)])
    assert m.op_status == "흑자전환"


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
