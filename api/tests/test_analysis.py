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


def test_topdown_flow_score_us_leads_weighting():
    # 미국 섹터 flow(가중 0.45)가 국내(0.40)보다 커서 미국 강세가 더 반영.
    high_us = analysis.topdown_flow_score(us_flow=100.0, kr_flow=0.0, kr_index_rising=None)
    high_kr = analysis.topdown_flow_score(us_flow=0.0, kr_flow=100.0, kr_index_rising=None)
    assert high_us > high_kr
    # 모두 최대 → 100.
    assert analysis.topdown_flow_score(100.0, 100.0, True) == 100.0
    # 전부 불명 → None.
    assert analysis.topdown_flow_score(None, None, None) is None


def test_topdown_flow_score_index_fallback():
    # 섹터 flow 를 못 구해도 지수 방향만으로 폴백 산출.
    assert analysis.topdown_flow_score(None, None, True) == 100.0
    assert analysis.topdown_flow_score(None, None, False) == 0.0


def test_overall_averages_present_scores():
    assert analysis.overall([80.0, None, 40.0]) == 60.0
    assert analysis.overall([None, None]) is None


def test_llm_comment_none_without_llm():
    # LLM 없으면(None) 네트워크 호출 없이 즉시 None.
    assert analysis.llm_comment(None, "m", "삼성전자", []) is None
