"""도메인 스코어링 규칙 순수 단위 테스트 — ORM·DB 없이 원시 수치로만 검증."""

from __future__ import annotations

from app.domain import scoring


def test_percentile_ranker_monotonic():
    rank = scoring.percentile_ranker([10.0, 20.0, 30.0, 40.0])
    assert rank(10.0) == 0.0
    assert rank(40.0) == 1.0
    assert 0.0 < rank(25.0) < 1.0
    assert rank(None) == 0.0


def test_cheap_ranker_lower_is_higher():
    rank = scoring.cheap_ranker([5.0, 10.0, 20.0, 40.0])
    assert rank(5.0) == 1.0  # 저평가일수록 높은 점수
    assert rank(40.0) == 0.0
    assert rank(None) == 0.0
    assert rank(-3.0) == 0.0  # 적자(음수)는 최하


def test_growth_score_high_above_low():
    rev = scoring.percentile_ranker([0.1, 0.5, 1.0])
    op = scoring.percentile_ranker([0.1, 0.5, 1.0])
    mom = scoring.percentile_ranker([0.0, 50.0, 100.0])
    high = scoring.growth_score(
        revenue_yoy=1.0, op_yoy=1.0, momentum_3m=100.0, op_turnaround=True,
        coverage_count=3, buy_count=3, rev_rank=rev, op_rank=op, mom_rank=mom,
    )
    low = scoring.growth_score(
        revenue_yoy=0.1, op_yoy=0.1, momentum_3m=0.0, op_turnaround=False,
        coverage_count=0, buy_count=0, rev_rank=rev, op_rank=op, mom_rank=mom,
    )
    assert high > low
    assert 0 <= low <= 100 and 0 <= high <= 100


def test_growth_score_turnaround_bonus():
    r = scoring.percentile_ranker([0.5, 0.5])
    m = scoring.percentile_ranker([10.0, 10.0])
    base = {"revenue_yoy": 0.5, "op_yoy": 0.5, "momentum_3m": 10.0,
            "coverage_count": 0, "buy_count": 0, "rev_rank": r, "op_rank": r, "mom_rank": m}
    assert scoring.growth_score(op_turnaround=True, **base) > scoring.growth_score(
        op_turnaround=False, **base
    )


def test_value_score_cheap_above_expensive():
    per = scoring.cheap_ranker([3.0, 10.0, 30.0])
    pbr = scoring.cheap_ranker([0.3, 1.0, 3.0])
    ev = scoring.cheap_ranker([3.0, 8.0, 20.0])
    cheap = scoring.value_score(
        per=3.0, pbr=0.3, ev_ebitda=3.0, roe=15.0, div_yield=None,
        per_rank=per, pbr_rank=pbr, ev_rank=ev,
    )
    pricey = scoring.value_score(
        per=30.0, pbr=3.0, ev_ebitda=20.0, roe=2.0, div_yield=None,
        per_rank=per, pbr_rank=pbr, ev_rank=ev,
    )
    assert cheap > pricey


def test_value_score_roe_and_div_bonus():
    r = scoring.cheap_ranker([10.0, 10.0])
    base = {"per": 10.0, "pbr": 10.0, "ev_ebitda": None, "per_rank": r, "pbr_rank": r, "ev_rank": r}
    assert scoring.value_score(roe=15.0, div_yield=None, **base) > scoring.value_score(
        roe=0.0, div_yield=None, **base
    )
    assert scoring.value_score(roe=None, div_yield=5.0, **base) > scoring.value_score(
        roe=None, div_yield=0.0, **base
    )
