"""HoldingRepository 의 SQLAlchemy 구현(단일 사용자 보유종목)."""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Holding
from app.ports.repositories import HoldingInput


class SqlHoldingRepository:
    """보유종목 CRUD(SQLAlchemy). ports.HoldingRepository 를 만족한다."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_all(self) -> list[Holding]:
        return list(
            self._db.scalars(select(Holding).order_by(Holding.stock_code)).all()
        )

    def get(self, stock_code: str) -> Holding | None:
        return self._db.scalar(select(Holding).where(Holding.stock_code == stock_code))

    def upsert(self, item: HoldingInput) -> Holding:
        row = self.get(item.stock_code)
        if row is None:
            row = Holding(stock_code=item.stock_code)
            self._db.add(row)
        row.shares = item.shares
        row.avg_cost = item.avg_cost
        row.stop_loss = item.stop_loss
        row.note = item.note
        self._db.commit()
        self._db.refresh(row)
        return row

    def delete(self, stock_code: str) -> bool:
        result = self._db.execute(sa_delete(Holding).where(Holding.stock_code == stock_code))
        self._db.commit()
        return (result.rowcount or 0) > 0
