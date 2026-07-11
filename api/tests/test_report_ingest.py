"""보고서 원문 백필 단위 테스트 — 대상 기간 산정·기간 문자열."""

from __future__ import annotations

from datetime import date

from app.services import report_ingest as ri


def test_target_reports_past_is_annual_only():
    # 2026-07 기준: 과거(≤2025)는 사업보고서만, 2026~ 는 반기/분기 추가.
    targets = ri._target_reports(date(2026, 7, 11))
    # 과거 연도는 annual 만.
    assert (2020, "annual") in targets
    assert (2020, "half") not in targets
    assert (2020, "quarter") not in targets
    # 2026 은 half·quarter 포함(annual 은 아직 미확정이라 제외 — year==today.year).
    assert (2026, "half") in targets
    assert (2026, "quarter") in targets
    assert (2026, "annual") not in targets


def test_target_reports_10yr_span():
    targets = ri._target_reports(date(2026, 7, 11))
    years = {y for y, _ in targets}
    assert min(years) == 2016  # 10년 전부터
    assert 2025 in years


def test_period_str():
    assert ri._period_str(2023, "annual") == "2023.12"
    assert ri._period_str(2026, "half") == "2026.06"
    assert ri._period_str(2026, "quarter") == "2026.03"
