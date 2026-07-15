"""분석 스코어(analysis) 순수 로직 단위 테스트."""

from __future__ import annotations

from app.services import analysis


def test_growth_score_high_when_strong():
    # 매출 +50% + 영업/순/EBITDA 흑자전환·마진 대폭개선 → 높은 점수.
    s = analysis.growth_score(0.5, "흑자전환", 0.30, "흑자전환", 0.30, "흑자전환", 0.30)
    assert s is not None and s >= 80


def test_growth_score_low_on_decline():
    s = analysis.growth_score(-0.2, "적자지속", -0.30, "적자지속", -0.30, "적자지속", -0.30)
    assert s is not None and s <= 10


def test_growth_score_none_when_no_data():
    assert analysis.growth_score(None, None) is None


def test_growth_score_turnaround_scored():
    # 흑전은 손익상태(흑자전환)+마진 회복으로 영업이익 축이 점수를 낸다.
    s = analysis.growth_score(None, "흑자전환", 0.30)
    assert s is not None and s > 0


def test_topdown_flow_score_us_leads_weighting():
    # 미국 섹터 flow(가중 0.45)가 국내(0.40)보다 커서 미국 강세가 더 반영. 지수도 0~100 수급 점수.
    high_us = analysis.topdown_flow_score(us_flow=100.0, kr_flow=0.0, kr_index_flow=None)
    high_kr = analysis.topdown_flow_score(us_flow=0.0, kr_flow=100.0, kr_index_flow=None)
    assert high_us > high_kr
    # 모두 최대 → 100.
    assert analysis.topdown_flow_score(100.0, 100.0, 100.0) == 100.0
    # 전부 불명 → None.
    assert analysis.topdown_flow_score(None, None, None) is None


def test_topdown_flow_score_index_fallback():
    # 섹터 flow 를 못 구해도 지수 수급 점수만으로 폴백 산출(가중치 재정규화).
    assert analysis.topdown_flow_score(None, None, 100.0) == 100.0
    assert analysis.topdown_flow_score(None, None, 0.0) == 0.0
    assert analysis.topdown_flow_score(None, None, 55.0) == 55.0


def test_overall_averages_present_scores():
    assert analysis.overall([80.0, None, 40.0]) == 60.0
    assert analysis.overall([None, None]) is None


def test_llm_comment_none_without_llm():
    # LLM 없으면(None) 네트워크 호출 없이 즉시 None.
    assert analysis.llm_comment(None, "m", "삼성전자", []) is None


def test_build_topdown_index_only_when_sector_unclassified(monkeypatch):
    # 섹터를 특정 못 해도 지수 수급은 항상 반영 — 지수 점수만으로 산출(가중치 100% 재정규화).
    monkeypatch.setattr(analysis.sector_etf, "themes_to_kr_sector", lambda names: None)
    monkeypatch.setattr(analysis.sector_etf, "kr_sector_to_us", lambda s: None)
    monkeypatch.setattr(analysis.sector_flow, "compute_flows", lambda market, session=None: [])
    monkeypatch.setattr(analysis.us_market, "fetch_kr_indices", lambda session=None: [])
    monkeypatch.setattr(analysis.sector_flow, "index_flow_score", lambda name, session=None: 72.0)

    view, score = analysis.build_topdown([], "KOSDAQ")
    assert score == 72.0  # 섹터 flow 없음 → 지수 수급(72)에 가중치 100% 재정규화
    assert view["kr_sector"] is None
    assert view["kr_index_flow"] == 72.0
