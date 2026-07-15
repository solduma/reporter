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
    op_status: str | None  # 흑자전환|흑자지속|적자전환|적자지속 (직전 대비 손익 상태)
    # 영업이익률 변화(당기 - 직전동기, 비율). 매출로 나눈 이익률이라 회사 규모 무관하게 마진 개선
    # 폭을 비교한다(0.559 = +55.9pp). 전 종목 OPM 개선 축 + 흑전 규모(op_yoy 정의 불가 시 대체)에 쓴다.
    op_margin_delta: float | None = None
    # 주당순이익 YoY. 증자로 주식 수가 늘어 주주가치가 희석되는 '속 빈 강정' 성장을 걸러내는 축.
    eps_yoy: float | None = None


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


def _margin_delta(
    latest_op: float | None,
    latest_rev: float | None,
    prior_op: float | None,
    prior_rev: float | None,
) -> float | None:
    """영업이익률 변화(당기 - 직전동기). 매출 정규화라 회사 규모와 무관한 흑자전환 규모 척도.

    매출이 0/음수/결측이면 이익률을 정의할 수 없어 None. 흑자전환뿐 아니라 모든 손익 상태에서
    이익률 개선 폭을 담지만, 스코어는 흑자전환에 한해 이 값을 가점 규모로 쓴다.
    """
    if latest_rev is None or prior_rev is None or latest_rev <= 0 or prior_rev <= 0:
        return None
    if latest_op is None or prior_op is None:
        return None
    return round(latest_op / latest_rev - prior_op / prior_rev, 4)


def compute_growth(stock_code: str, periods: list) -> GrowthMetric | None:
    """financials 행 리스트(각 .period/.revenue/.operating_income/.eps)로 성장지표를 만든다.

    실적(추정치 제외) 분기 중 최신을 기준으로, 1년 전 동분기(4기 전)와 비교한다.
    """
    actuals = [p for p in periods if _key(p.period) and not _is_estimate(p.period)]
    if not actuals:
        return None
    actuals.sort(key=lambda p: _key(p.period))

    latest = actuals[-1]
    ly, lm = _key(latest.period)
    prior = next((p for p in actuals if _key(p.period) == (ly - 1, lm)), None)

    op_status = _op_status(
        prior.operating_income if prior else None,
        latest.operating_income,
    )
    op_turnaround = op_status == "흑자전환"

    return GrowthMetric(
        stock_code=stock_code,
        period=latest.period,
        revenue_yoy=_yoy(latest.revenue, prior.revenue) if prior else None,
        op_yoy=_yoy(latest.operating_income, prior.operating_income) if prior else None,
        op_turnaround=op_turnaround,
        op_status=op_status,
        op_margin_delta=_margin_delta(
            latest.operating_income, latest.revenue,
            prior.operating_income, prior.revenue,
        ) if prior else None,
        eps_yoy=_yoy(
            getattr(latest, "eps", None),
            getattr(prior, "eps", None),
        ) if prior else None,
    )


def _op_status(prior_op: float | None, latest_op: float | None) -> str | None:
    """직전 동기 대비 영업이익 손익 상태. 0 은 적자(비흑자)로 본다. 결측이면 None.

    흑자전환(적자→흑자)·흑자지속·적자전환(흑자→적자)·적자지속 4상태로 나눠, '흑자전환 아님'이
    계속흑자·적자전환·계속적자를 뭉개던 표시 손실을 없앤다.
    """
    if prior_op is None or latest_op is None:
        return None
    prior_pos = prior_op > 0
    latest_pos = latest_op > 0
    if prior_pos and latest_pos:
        return "흑자지속"
    if not prior_pos and latest_pos:
        return "흑자전환"
    if prior_pos and not latest_pos:
        return "적자전환"
    return "적자지속"
