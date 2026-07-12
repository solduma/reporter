"""보유종목 손익·손절선·비중 순수 계산 — IO 없음(값만 받아 값 반환).

현재가·평단·수량 같은 원시값을 받아 평가손익·수익률·손절 상태·섹터 비중을 계산한다. 서비스가
DB 에서 값을 뽑아 이 함수들에 넘기고, 결과를 DTO 로 조립한다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 손절선 근접 임계 — 현재가가 손절선의 이 비율 이내로 내려오면 'near'(경고).
_STOP_NEAR_RATIO = 0.05


@dataclass
class HoldingCalc:
    """한 보유종목의 파생 계산 결과. 현재가 미확보 시 손익 관련은 None."""

    current_price: float | None
    market_value: float | None  # 현재가 x 수량
    cost_basis: float  # 평단 x 수량
    pnl: float | None  # 평가손익 = 평가액 - 원가
    pnl_pct: float | None  # 수익률(%) = pnl / 원가 x 100
    stop_status: str  # "none"(손절선 미설정) | "ok" | "near" | "hit"


def compute_holding(
    shares: float, avg_cost: float, current_price: float | None, stop_loss: float | None
) -> HoldingCalc:
    """보유 1건의 손익·손절 상태. current_price 없으면 손익은 None, cost_basis 만 산출."""
    cost_basis = shares * avg_cost
    if current_price is None:
        return HoldingCalc(None, None, cost_basis, None, None, _stop_status(None, stop_loss))
    market_value = shares * current_price
    pnl = market_value - cost_basis
    pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else None
    return HoldingCalc(
        current_price=current_price,
        market_value=market_value,
        cost_basis=cost_basis,
        pnl=pnl,
        pnl_pct=round(pnl_pct, 2) if pnl_pct is not None else None,
        stop_status=_stop_status(current_price, stop_loss),
    )


def _stop_status(current_price: float | None, stop_loss: float | None) -> str:
    if stop_loss is None or stop_loss <= 0:
        return "none"
    if current_price is None:
        return "ok"  # 현재가 모르면 판단 보류(안전측)
    if current_price <= stop_loss:
        return "hit"
    if current_price <= stop_loss * (1 + _STOP_NEAR_RATIO):
        return "near"
    return "ok"


@dataclass
class PortfolioSummary:
    total_value: float  # 총 평가액(현재가 확보분만 합산)
    total_cost: float  # 총 원가(전체)
    total_pnl: float  # 총 평가손익(현재가 확보분)
    total_pnl_pct: float | None  # 손익률(%)
    stop_hit: int  # 손절 도달 종목 수
    stop_near: int  # 손절 근접 종목 수


def summarize(calcs: list[HoldingCalc]) -> PortfolioSummary:
    """보유 계산 결과들을 포트폴리오 요약으로 집계. 현재가 확보분만 평가액·손익에 합산."""
    total_value = sum(c.market_value for c in calcs if c.market_value is not None)
    total_pnl = sum(c.pnl for c in calcs if c.pnl is not None)
    # 손익률 분모는 '현재가가 있어 손익을 계산한' 종목의 원가만(평가액과 짝이 맞게).
    priced_cost = sum(c.cost_basis for c in calcs if c.pnl is not None)
    total_cost = sum(c.cost_basis for c in calcs)
    return PortfolioSummary(
        total_value=total_value,
        total_cost=total_cost,
        total_pnl=total_pnl,
        total_pnl_pct=round(total_pnl / priced_cost * 100, 2) if priced_cost > 0 else None,
        stop_hit=sum(1 for c in calcs if c.stop_status == "hit"),
        stop_near=sum(1 for c in calcs if c.stop_status == "near"),
    )


def sector_weights(items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """(섹터, 평가액) 목록 → 섹터별 평가액 비중(%) 내림차순. 평가액 합 0이면 빈 리스트."""
    total = sum(v for _, v in items if v > 0)
    if total <= 0:
        return []
    agg: dict[str, float] = {}
    for sector, value in items:
        if value > 0:
            agg[sector] = agg.get(sector, 0.0) + value
    weights = [(s, round(v / total * 100, 1)) for s, v in agg.items()]
    return sorted(weights, key=lambda x: x[1], reverse=True)
