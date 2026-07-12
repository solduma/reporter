"""보유종목(포트폴리오) 응용 서비스 — 단일 사용자 상태.

라우터는 이 서비스만 호출하고, 데이터 접근은 HoldingRepository 포트 경유(seam 으로 주입 가능).
1단계는 CRUD 만 — 손익·손절선 판단 등은 후속에서 현재가와 결합한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.adapters.persistence import SqlHoldingRepository
from app.db.models import Holding
from app.domain import portfolio as calc
from app.ports.repositories import HoldingRepository
from app.schemas import (
    HoldingOut,
    PortfolioSummaryOut,
    PortfolioView,
    SectorWeightOut,
)
from app.services import company_service


# 포트 공급자 seam — 기본은 SqlHoldingRepository, 테스트가 훅 교체로 fake 주입 가능.
def _repo(db: Session) -> HoldingRepository:
    return SqlHoldingRepository(db)


@dataclass
class HoldingInput:
    """보유종목 저장 입력(HoldingRepository.upsert 용 구조적 타입 충족)."""

    stock_code: str
    shares: float
    avg_cost: float
    stop_loss: float | None = None
    note: str | None = None


def _current_price(db: Session, code: str) -> float | None:
    """최근 유니버스 스냅샷 종가(일일 배치가 채움). 손익·손절 계산의 현재가."""
    snap = company_service.latest_snapshot(db, code)
    return float(snap.close_price) if snap and snap.close_price else None


def _to_out(db: Session, h: Holding) -> HoldingOut:
    """ORM 행 → 응답 DTO(종목명 해석 + 손익·손절 계산). 조립을 서비스가 소유(라우터는 db.models 모름)."""
    c = calc.compute_holding(h.shares, h.avg_cost, _current_price(db, h.stock_code), h.stop_loss)
    return HoldingOut(
        stock_code=h.stock_code,
        stock_name=company_service.resolve_stock_name(db, h.stock_code),
        shares=h.shares,
        avg_cost=h.avg_cost,
        stop_loss=h.stop_loss,
        note=h.note,
        updated_at=h.updated_at,
        current_price=c.current_price,
        market_value=c.market_value,
        cost_basis=c.cost_basis,
        pnl=c.pnl,
        pnl_pct=c.pnl_pct,
        stop_status=c.stop_status,
    )


def list_holdings(db: Session) -> list[HoldingOut]:
    return [_to_out(db, h) for h in _repo(db).list_all()]


def save_holding(db: Session, item: HoldingInput) -> HoldingOut:
    return _to_out(db, _repo(db).upsert(item))


def delete_holding(db: Session, stock_code: str) -> bool:
    return _repo(db).delete(stock_code)


def _representative_sector(db: Session, code: str) -> str:
    """보유종목의 대표 섹터명(judal 테마 첫 항목). 매핑 없으면 '기타'."""
    names = company_service.theme_names(db, code)
    return names[0] if names else "기타"


def portfolio_view(db: Session) -> PortfolioView:
    """보유목록 + 요약 + 섹터분산. 손익은 현재가 확보분만 집계."""
    outs = list_holdings(db)
    calcs = [
        calc.HoldingCalc(
            current_price=o.current_price,
            market_value=o.market_value,
            cost_basis=o.cost_basis,
            pnl=o.pnl,
            pnl_pct=o.pnl_pct,
            stop_status=o.stop_status,
        )
        for o in outs
    ]
    s = calc.summarize(calcs)
    sector_items = [
        (_representative_sector(db, o.stock_code), o.market_value or 0.0) for o in outs
    ]
    weights = calc.sector_weights(sector_items)
    return PortfolioView(
        holdings=outs,
        summary=PortfolioSummaryOut(
            total_value=s.total_value,
            total_cost=s.total_cost,
            total_pnl=s.total_pnl,
            total_pnl_pct=s.total_pnl_pct,
            stop_hit=s.stop_hit,
            stop_near=s.stop_near,
        ),
        sectors=[SectorWeightOut(sector=sec, weight_pct=w) for sec, w in weights],
    )
