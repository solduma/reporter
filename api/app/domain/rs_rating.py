"""IBD 스타일 RS Rating(1~99) — 전 종목 대비 가격 모멘텀 백분위. 순수 도메인.

강도지수(StrengthFactor)는 최근 분기를 2배 가중한 4개 분기 수익률 합:
  SF = 0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252)
  ROC(k) = 종가(t)/종가(t-k) - 1   (63/126/189/252 거래일 = 4개 분기)
이 SF 를 전 유니버스에서 백분위(1~99)로 환산해 '얼마나 강한 주도주인가'를 1~99 로 준다.
80↑ 이 IBD 실전 매수 후보. I/O 없음(순수).
"""

from __future__ import annotations

# (거래일 오프셋, 가중치). 최근 분기(63일)를 2배 가중.
_ROC_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))
_MIN_BARS = 252  # 4개 분기 수익률을 모두 계산하려면 최소 1년치 봉 필요


def strength_factor(closes: list[float]) -> float | None:
    """종가(날짜 오름차순)로 가중 ROC 강도지수를 계산한다. 1년치 미만이면 None."""
    n = len(closes)
    if n < _MIN_BARS + 1:
        return None
    last = closes[-1]
    total = 0.0
    for offset, weight in _ROC_WEIGHTS:
        past = closes[-1 - offset]
        if past <= 0:
            return None
        total += weight * (last / past - 1)
    return total


def to_rating(factor: float | None, sorted_factors: list[float]) -> int | None:
    """강도지수를 전 유니버스 백분위 1~99 로 환산한다. factor·표본 부족 시 None.

    sorted_factors 는 오름차순 정렬된 전 종목 강도지수(결측 제외). 큰 factor 일수록 높은 rating.
    """
    if factor is None:
        return None
    n = len(sorted_factors)
    if n <= 1:
        return None
    below = sum(1 for f in sorted_factors if f < factor)
    pct = below / (n - 1)  # 0~1
    return max(1, min(99, round(pct * 98) + 1))  # 1~99
