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
    # 흑자전환 규모 미상(Δ None)이면 기존 이진 가점과 동일(폴백 만점 배수).
    assert s.growth_score(None, None, True) == 15.0


def test_turnaround_scale_by_magnitude():
    # Δ 미상은 1.0(폴백). 3pp 이하는 하한 0.2, 30pp 이상은 만점 1.0, 중간은 선형.
    assert s.turnaround_scale(None) == 1.0
    assert s.turnaround_scale(0.0) == 0.2  # 3pp 미만 → band 0 → 하한
    assert s.turnaround_scale(0.03) == 0.2  # 밴드 하단
    assert s.turnaround_scale(0.30) == 1.0  # 밴드 상단(만점)
    mid = s.turnaround_scale(0.165)  # 중간(3~30pp 의 절반)
    assert 0.59 < mid < 0.61


def test_growth_score_turnaround_magnitude_matters():
    # 규모 큰 흑전(+30pp)이 규모 작은 흑전(+3pp)보다 높은 가점을 받는다.
    big = s.growth_score(None, None, True, 0.30)
    small = s.growth_score(None, None, True, 0.03)
    assert big == 15.0  # 만점 가점
    assert small == 3.0  # 0.15 * 0.2 하한
    assert big > small


def test_overall_average():
    assert s.overall([80.0, 60.0, None]) == 70.0
    assert s.overall([None, None]) is None


def test_topdown_flow_us_weighted_higher_than_kr():
    high_us = s.topdown_flow_score(us_flow=100.0, kr_flow=0.0, kr_index_flow=None)
    high_kr = s.topdown_flow_score(us_flow=0.0, kr_flow=100.0, kr_index_flow=None)
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


def test_value_score_absolute_band():
    # 저PBR·저PER·저EV(백분위 1)+ROE·배당 가점 → 만점 근처. 결측은 재정규화로 흡수.
    hi = s.value_score(per=5, pbr=0.5, ev_ebitda=3, roe=20, div_yield=6,
                       per_rank=1.0, pbr_rank=1.0, ev_rank=1.0)
    lo = s.value_score(per=50, pbr=5, ev_ebitda=30, roe=1, div_yield=0,
                       per_rank=0.0, pbr_rank=0.0, ev_rank=0.0)
    assert hi is not None and lo is not None and hi > lo
    assert hi >= 90  # 저평가 백분위 만점 + 가점
    # 전부 결측 → None.
    assert s.value_score(None, None, None, None, None, None, None, None) is None
    # 일부만 있어도 남은 가중치로 재정규화(PBR 백분위 1 만 있으면 100).
    only_pbr = s.value_score(per=None, pbr=0.5, ev_ebitda=None, roe=None, div_yield=None,
                             per_rank=None, pbr_rank=1.0, ev_rank=None)
    assert only_pbr == 100.0


def test_topdown_stock_rs_differentiates():
    # 같은 섹터 flow 라도 종목 RS 가 다르면 탑다운 점수가 갈린다(섹터별 뭉침 보정).
    base = {"us_flow": 50.0, "kr_flow": 50.0, "kr_index_flow": 50.0}
    hi = s.topdown_flow_score(**base, stock_rs=90.0)
    lo = s.topdown_flow_score(**base, stock_rs=10.0)
    assert hi > lo
    # stock_rs 없으면 섹터만으로(하위호환).
    none_rs = s.topdown_flow_score(**base)
    assert none_rs is not None and lo < none_rs < hi
    # 섹터 전무 + RS 만 있으면 RS 만으로.
    assert s.topdown_flow_score(None, None, None, stock_rs=80.0) == 80.0
