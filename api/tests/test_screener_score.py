"""스크리너 성장스코어·커버리지 라벨 순수 로직 단위 테스트."""

from __future__ import annotations

from dataclasses import dataclass

from app.routers import screener


@dataclass
class _U:
    momentum_3m: float | None


@dataclass
class _G:
    revenue_yoy: float | None
    op_yoy: float | None
    op_turnaround: bool


@dataclass
class _F:
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    ev_ebitda: float | None = None
    div_yield: float | None = None


def test_coverage_label():
    assert screener._coverage_label(0, 0) is None  # 커버 없음
    assert screener._coverage_label(3, 0) == "HOLD"  # 커버 있으나 BUY 없음
    assert screener._coverage_label(3, 2) == "BUY"  # BUY 있음


def test_percentile_ranker_monotonic():
    rank = screener._percentile_ranker([10.0, 20.0, 30.0, 40.0])
    assert rank(10.0) == 0.0  # 최저 → 0
    assert rank(40.0) == 1.0  # 최고 → 1
    assert 0.0 < rank(25.0) < 1.0
    assert rank(None) == 0.0  # 결측 → 최하


def test_percentile_ranker_small_sample():
    rank = screener._percentile_ranker([5.0])
    assert rank(5.0) == 0.5  # 소표본은 중립
    assert rank(None) == 0.0


def test_growth_score_ranks_high_growth_above_low():
    rev_rank = screener._percentile_ranker([0.1, 0.5, 1.0])
    op_rank = screener._percentile_ranker([0.1, 0.5, 1.0])
    mom_rank = screener._percentile_ranker([0.0, 50.0, 100.0])

    high = screener._growth_score(
        _U(100.0), _G(1.0, 1.0, False), 3, 3, rev_rank, op_rank, mom_rank
    )
    low = screener._growth_score(
        _U(0.0), _G(0.1, 0.1, False), 0, 0, rev_rank, op_rank, mom_rank
    )
    assert high > low
    assert 0 <= low <= 100 and 0 <= high <= 100


def test_growth_score_buy_coverage_boosts():
    rev_rank = op_rank = screener._percentile_ranker([0.5, 0.5])
    mom_rank = screener._percentile_ranker([10.0, 10.0])
    g = _G(0.5, 0.5, False)
    covered_buy = screener._growth_score(_U(10.0), g, 4, 4, rev_rank, op_rank, mom_rank)
    uncovered = screener._growth_score(_U(10.0), g, 0, 0, rev_rank, op_rank, mom_rank)
    # 커버리지+BUY 는 센티먼트·커버리지 factor 가점으로 더 높아야 한다
    assert covered_buy > uncovered


def test_growth_score_null_growth_low():
    # 성장지표 없는 종목(g=None)은 모멘텀만 반영돼 낮은 스코어
    rev_rank = op_rank = screener._percentile_ranker([0.5])
    mom_rank = screener._percentile_ranker([10.0])
    score = screener._growth_score(_U(10.0), None, 0, 0, rev_rank, op_rank, mom_rank)
    assert score <= 20  # 모멘텀(0.15)만 최대


# ── 가치 전략 ──────────────────────────────────────────────────────────
def test_cheap_ranker_lower_is_higher():
    # 저평가 백분위: 값이 작을수록 1.0(PER/PBR 처럼 낮을수록 좋은 지표).
    rank = screener._cheap_ranker([5.0, 10.0, 20.0, 40.0])
    assert rank(5.0) == 1.0  # 최저 → 최고 점수
    assert rank(40.0) == 0.0  # 최고 → 최저 점수
    assert rank(None) == 0.0  # 결측
    assert rank(-3.0) == 0.0  # 음수(적자 PER 등) → 최하


def test_value_score_cheap_above_expensive():
    per_rank = screener._cheap_ranker([3.0, 10.0, 30.0])
    pbr_rank = screener._cheap_ranker([0.3, 1.0, 3.0])
    ev_rank = screener._cheap_ranker([3.0, 8.0, 20.0])
    cheap = screener._value_score(_F(per=3.0, pbr=0.3, roe=15.0, ev_ebitda=3.0), per_rank, pbr_rank, ev_rank)
    pricey = screener._value_score(_F(per=30.0, pbr=3.0, roe=2.0, ev_ebitda=20.0), per_rank, pbr_rank, ev_rank)
    assert cheap > pricey
    assert 0 <= pricey <= 100 and 0 <= cheap <= 100


def test_value_score_none_is_zero():
    per_rank = pbr_rank = ev_rank = screener._cheap_ranker([10.0, 10.0])
    assert screener._value_score(None, per_rank, pbr_rank, ev_rank) == 0.0


def test_value_score_roe_bonus():
    # ROE 가 높으면 가점(같은 밸류 배수라도).
    per_rank = pbr_rank = ev_rank = screener._cheap_ranker([10.0, 10.0])
    hi = screener._value_score(_F(per=10.0, pbr=10.0, roe=15.0), per_rank, pbr_rank, ev_rank)
    lo = screener._value_score(_F(per=10.0, pbr=10.0, roe=0.0), per_rank, pbr_rank, ev_rank)
    assert hi > lo


def test_value_score_dividend_bonus():
    # 시가배당률이 높으면 가점.
    per_rank = pbr_rank = ev_rank = screener._cheap_ranker([10.0, 10.0])
    hi = screener._value_score(_F(per=10.0, pbr=10.0, div_yield=5.0), per_rank, pbr_rank, ev_rank)
    lo = screener._value_score(_F(per=10.0, pbr=10.0, div_yield=0.0), per_rank, pbr_rank, ev_rank)
    assert hi > lo


# ── 이벤트 전략 ────────────────────────────────────────────────────────
def test_event_kind_label_map():
    # event_kind 쿼리 파라미터 → 표시 라벨 매핑.
    assert screener._EVENT_KIND_LABEL["disclosure"] == "공시"
    assert screener._EVENT_KIND_LABEL["surge"] == "급등락"
    assert screener._EVENT_KIND_LABEL["broadcast"] == "브리핑"
    assert screener._EVENT_KIND_LABEL["report"] == "리포트"
