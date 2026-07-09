"""분석 스코어(analysis) 순수 로직 단위 테스트."""

from __future__ import annotations

from app.services import analysis


def test_growth_score_high_when_strong_yoy():
    # 매출 +50%, 영업이익 +60%, 흑자전환 → 높은 점수.
    s = analysis.growth_score(0.5, 0.6, True)
    assert s is not None and s >= 80


def test_growth_score_low_on_decline():
    s = analysis.growth_score(-0.2, -0.2, False)
    assert s is not None and s <= 10


def test_growth_score_none_when_no_data():
    assert analysis.growth_score(None, None, False) is None


def test_growth_score_turnaround_only():
    # YoY 결측이라도 흑자전환이면 가점만으로 점수 산출.
    s = analysis.growth_score(None, None, True)
    assert s is not None and s > 0


def test_topdown_score_us_leads_weighting():
    # 미국 상승·국내 하락 → 미국 가중(0.6)이 커서 중립 이상.
    s = analysis.topdown_score(us_rising=True, kr_rising=False)
    assert s == 60.0
    # 둘 다 상승 → 100.
    assert analysis.topdown_score(True, True) == 100.0
    # 둘 다 불명 → None.
    assert analysis.topdown_score(None, None) is None


def test_overall_averages_present_scores():
    assert analysis.overall([80.0, None, 40.0]) == 60.0
    assert analysis.overall([None, None]) is None


def test_llm_comment_none_without_key():
    # 키 없으면 네트워크 호출 없이 즉시 None.
    assert analysis.llm_comment("", "", "m", "삼성전자", []) is None
