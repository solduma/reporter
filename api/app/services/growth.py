"""성장지표 계산 — 분기 재무(financials)에서 YoY·흑자전환 파생.

period 는 'YYYY.MM' 또는 'YYYY.MM(E)'(추정치). 같은 분기 4기 전(=1년 전 동분기)
대비 매출/영업이익 성장률을 계산한다. 추정치는 실적치와 섞이지 않게 별도 취급.
순수 계산 로직(I/O 없음).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PERIOD_RE = re.compile(r"^(\d{4})\.(\d{2})")


@dataclass
class GrowthMetric:
    stock_code: str
    period: str  # 기준 분기(최신 실적)
    revenue_yoy: float | None  # 매출 YoY (0.28 = +28%)
    op_yoy: float | None  # 영업이익 YoY
    op_turnaround: bool  # 직전 동기 적자 → 당기 흑자


def _key(period: str) -> tuple[int, int] | None:
    """정렬용 (year, month). 추정치 표기 '(E)' 는 무시."""
    m = _PERIOD_RE.match(period)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _is_estimate(period: str) -> bool:
    return "(E)" in period or "(e)" in period


def _yoy(curr: float | None, prior: float | None) -> float | None:
    """YoY 성장률. prior 가 0/음수/결측이면 비율이 왜곡되므로 None(흑전은 별도 플래그)."""
    if curr is None or prior is None or prior <= 0:
        return None
    return round((curr - prior) / prior, 4)


def compute_growth(stock_code: str, periods: list) -> GrowthMetric | None:
    """financials 행 리스트(각 .period/.revenue/.operating_income)로 성장지표를 만든다.

    실적(추정치 제외) 분기 중 최신을 기준으로, 1년 전 동분기(4기 전)와 비교한다.
    """
    actuals = [p for p in periods if _key(p.period) and not _is_estimate(p.period)]
    if not actuals:
        return None
    actuals.sort(key=lambda p: _key(p.period))

    latest = actuals[-1]
    ly, lm = _key(latest.period)
    prior = next((p for p in actuals if _key(p.period) == (ly - 1, lm)), None)

    op_turnaround = bool(
        prior
        and prior.operating_income is not None
        and latest.operating_income is not None
        and prior.operating_income <= 0 < latest.operating_income
    )

    return GrowthMetric(
        stock_code=stock_code,
        period=latest.period,
        revenue_yoy=_yoy(latest.revenue, prior.revenue) if prior else None,
        op_yoy=_yoy(latest.operating_income, prior.operating_income) if prior else None,
        op_turnaround=op_turnaround,
    )
