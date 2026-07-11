"""도메인 분석·flow·로테이션 스코어링 순수 단위 테스트."""

from __future__ import annotations

from app.domain import analysis_scoring as s


def test_band_normalizes_and_clamps():
    assert s.band(None, 0, 10) is None
    assert s.band(0, 0, 10) == 0.0
    assert s.band(10, 0, 10) == 1.0
    assert s.band(5, 0, 10) == 0.5
    assert s.band(-5, 0, 10) == 0.0  # 하한 클램프
    assert s.band(99, 0, 10) == 1.0  # 상한 클램프


def test_growth_score_matches_legacy_band():
    # 레거시: (yoy+0.2)/0.8 클램프. rev 0.6·op 0.6 → 1.0 각각, 흑전 없음.
    assert s.growth_score(0.6, 0.6, False) == 100.0
    assert s.growth_score(-0.2, -0.2, False) == 0.0
    assert s.growth_score(None, None, False) is None  # 데이터 전무
    # 흑자전환만 있어도 점수(가점 0.15).
    assert s.growth_score(None, None, True) == 15.0


def test_overall_average():
    assert s.overall([80.0, 60.0, None]) == 70.0
    assert s.overall([None, None]) is None


def test_topdown_flow_us_weighted_higher_than_kr():
    high_us = s.topdown_flow_score(us_flow=100.0, kr_flow=0.0, kr_index_rising=None)
    high_kr = s.topdown_flow_score(us_flow=0.0, kr_flow=100.0, kr_index_rising=None)
    assert high_us > high_kr  # 미국 선행 가중(0.45>0.40)
    assert s.topdown_flow_score(None, None, None) is None


def test_flow_score_strong_vs_laggard():
    strong = s.flow_score(return_3m=40, near_high_pct=100, vol_ratio=2.0, foreign_delta=1.0)
    laggard = s.flow_score(return_3m=-20, near_high_pct=70, vol_ratio=0.5, foreign_delta=-1.0)
    assert strong == 100.0
    assert laggard == 0.0
    assert s.flow_score(None, None, None, None) is None


def test_foreign_delta_change():
    assert s.foreign_delta([3.0, 4.0, 5.0], lookback=2) == 2.0
    assert s.foreign_delta([5.0]) is None


def test_rotation_score():
    # 센티먼트 +1(최대)·커버리지 최대 → 100.
    assert s.rotation_score(1.0, 10, 10) == 100.0
    # 센티먼트 -1(최소)·커버리지 0 → 0.
    assert s.rotation_score(-1.0, 0, 10) == 0.0
    assert s.rotation_score(0.0, 0, 0) == 35.0  # 중립 센티먼트 0.7*0.5=0.35, max_count 0 방어


def test_float_association_matches_legacy():
    # 회귀: 항 그룹핑을 바꾸면 마지막 소수 자리가 달라진다(레거시 인라인식과 정확히 일치해야).
    assert s.flow_score(return_3m=-18.5, near_high_pct=70.1, vol_ratio=0.5, foreign_delta=0.27) == 7.5
    assert s.rotation_score(0.0, 19, 24) == 58.8
    assert s.rotation_score(-1.0, 7, 40) == 5.3


def test_flow_strength_thresholds():
    assert s.flow_strength(None) is None
    assert s.flow_strength(75) == "strong"
    assert s.flow_strength(60) == "strong"  # 경계 포함
    assert s.flow_strength(50) == "moderate"
    assert s.flow_strength(40) == "moderate"  # 경계 포함
    assert s.flow_strength(30) == "weak"


def test_sentiment_score_mapping():
    assert s.SENTIMENT_SCORE["BUY"] == 1.0
    assert s.SENTIMENT_SCORE["HOLD"] == 0.0
    assert s.SENTIMENT_SCORE["SELL"] == -1.0
