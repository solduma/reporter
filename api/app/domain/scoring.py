"""스크리너 스코어링 규칙 — 순수 도메인 로직(성장·가치 전략).

백분위 랭커와 전략별 스코어 함수. 영속화·ORM·프레임워크를 모른다 — 입력은 원시 수치이고
호출측(서비스/라우터)이 ORM 행에서 값을 뽑아 넘긴다. 이 분리로 규칙을 DB 없이 단위테스트한다.
"""

from __future__ import annotations

from collections.abc import Callable

Ranker = Callable[[float | None], float]


def percentile_ranker(values: list[float | None]) -> Ranker:
    """값 리스트에 대해 백분위(0~1) 함수를 만든다(클수록 1.0). 결측·소표본에 강건."""
    clean = sorted(v for v in values if v is not None)
    n = len(clean)
    if n <= 1:
        return lambda v: 0.5 if v is not None else 0.0

    def rank(v: float | None) -> float:
        if v is None:
            return 0.0
        lo = sum(1 for c in clean if c < v)
        return lo / (n - 1)

    return rank


def cheap_ranker(values: list[float | None]) -> Ranker:
    """저평가 백분위(작을수록 1.0). PER/PBR/EV-EBITDA 처럼 낮을수록 좋은 지표용. 양수만."""
    clean = sorted(v for v in values if v is not None and v > 0)
    n = len(clean)
    if n <= 1:
        return lambda v: 0.5 if (v is not None and v > 0) else 0.0

    def rank(v: float | None) -> float:
        if v is None or v <= 0:  # 결측·적자(음수 PER 등)는 최하위
            return 0.0
        hi = sum(1 for c in clean if c > v)
        return hi / (n - 1)  # 작을수록 1.0

    return rank


def growth_score(
    *,
    revenue_yoy: float | None,
    op_yoy: float | None,
    momentum_3m: float | None,
    op_turnaround: bool,
    coverage_count: int,
    buy_count: int,
    rev_rank: Ranker,
    op_rank: Ranker,
    mom_rank: Ranker,
) -> float:
    """성장스코어(0~100). YoY 백분위 + 모멘텀 + 흑전 + 센티먼트·커버리지 factor."""
    rev = rev_rank(revenue_yoy)
    op = op_rank(op_yoy)
    mom = mom_rank(momentum_3m)
    turn_bonus = 0.10 if op_turnaround else 0.0
    sentiment_factor = (buy_count / coverage_count) if coverage_count else 0.0
    coverage_factor = 1.0 if coverage_count else 0.0
    score = (
        0.30 * rev + 0.25 * op + 0.15 * mom + turn_bonus
        + 0.12 * sentiment_factor + 0.08 * coverage_factor
    )
    return round(min(score, 1.0) * 100, 1)


def value_score(
    *,
    per: float | None,
    pbr: float | None,
    ev_ebitda: float | None,
    roe: float | None,
    div_yield: float | None,
    per_rank: Ranker,
    pbr_rank: Ranker,
    ev_rank: Ranker,
) -> float:
    """가치스코어(0~100). 저PER·저PBR·저EV-EBITDA 백분위 + 고ROE·고배당 가점.

    저PBR 을 가장 무겁게(자산가치 기준), 저PER·저EV/EBITDA 를 수익가치로. ROE·배당은 우량 가점.
    """
    per_r = per_rank(per)
    pbr_r = pbr_rank(pbr)
    ev_r = ev_rank(ev_ebitda)
    # ROE 절대 기준 가점(15% 이상 만점, % 값). 배당 가점(5% 이상 만점, 시가배당률 %).
    roe_bonus = 0.0
    if roe is not None:
        roe_bonus = max(0.0, min(roe / 15.0, 1.0)) * 0.12
    div_bonus = 0.0
    if div_yield is not None:
        div_bonus = max(0.0, min(div_yield / 5.0, 1.0)) * 0.08
    score = 0.35 * pbr_r + 0.28 * per_r + 0.17 * ev_r + roe_bonus + div_bonus
    return round(min(score, 1.0) * 100, 1)


def us_screen_score(
    *,
    per: float | None,
    pbr: float | None,
    momentum_3m: float | None,
    near_high_pct: float | None,
    per_rank: Ranker,
    pbr_rank: Ranker,
    mom_rank: Ranker,
) -> float:
    """US 스크리너 종합 스코어(0~100). 저PER·저PBR(집합 내 백분위) + 모멘텀 + 신고가 근접.

    가치(저평가)와 모멘텀(주도력)을 함께 본다: 저PBR 0.30 + 저PER 0.25 + 모멘텀 0.30 +
    신고가근접 0.15. per/pbr 랭커는 cheap_ranker(작을수록 1.0), mom 은 percentile_ranker.
    """
    per_r = per_rank(per)
    pbr_r = pbr_rank(pbr)
    mom_r = mom_rank(momentum_3m)
    # 신고가 근접(0.7~1.0 → 0~1). near_high_pct 는 종가/52주고가 비율(%)로 호출측이 넘긴다.
    nh = 0.0
    if near_high_pct is not None:
        nh = max(0.0, min((near_high_pct / 100 - 0.7) / 0.3, 1.0))
    score = 0.30 * pbr_r + 0.25 * per_r + 0.30 * mom_r + 0.15 * nh
    return round(min(score, 1.0) * 100, 1)
