"""보유종목(포트폴리오) 응용 서비스 — 단일 사용자 상태.

라우터는 이 서비스만 호출하고, 데이터 접근은 HoldingRepository 포트 경유(seam 으로 주입 가능).
1단계는 CRUD 만 — 손익·손절선 판단 등은 후속에서 현재가와 결합한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.adapters.persistence import SqlHoldingRepository
from app.db.models import Holding
from app.ports.repositories import HoldingRepository
from app.schemas import HoldingOut
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


def _to_out(db: Session, h: Holding) -> HoldingOut:
    """ORM 행 → 응답 DTO(종목명 해석 포함). 라우터가 db.models 를 모르도록 조립을 서비스가 소유."""
    return HoldingOut(
        stock_code=h.stock_code,
        stock_name=company_service.resolve_stock_name(db, h.stock_code),
        shares=h.shares,
        avg_cost=h.avg_cost,
        stop_loss=h.stop_loss,
        note=h.note,
        updated_at=h.updated_at,
    )


def list_holdings(db: Session) -> list[HoldingOut]:
    return [_to_out(db, h) for h in _repo(db).list_all()]


def save_holding(db: Session, item: HoldingInput) -> HoldingOut:
    return _to_out(db, _repo(db).upsert(item))


def delete_holding(db: Session, stock_code: str) -> bool:
    return _repo(db).delete(stock_code)
