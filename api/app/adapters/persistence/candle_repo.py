"""CandleRepository 의 SQLAlchemy 구현 — 봉 영속화(읽기/upsert)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import PriceCandle, PriceCandleIntraday, Timeframe
from app.ports.repositories import CandleInput


class SqlCandleRepository:
    """봉 영속화(SQLAlchemy). ports.CandleRepository 를 만족한다."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def read_periodic(self, code: str, tf: str) -> list[PriceCandle]:
        return list(
            self._db.scalars(
                select(PriceCandle)
                .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe(tf))
                .order_by(PriceCandle.bar_date)
            ).all()
        )

    def read_intraday(self, code: str, days: int = 14) -> list[PriceCandleIntraday]:
        window_start = datetime.now() - timedelta(days=days)
        return list(
            self._db.scalars(
                select(PriceCandleIntraday)
                .where(
                    PriceCandleIntraday.stock_code == code,
                    PriceCandleIntraday.bar_ts >= window_start,
                )
                .order_by(PriceCandleIntraday.bar_ts)
            ).all()
        )

    def latest_bar_date(self, code: str, tf: Timeframe) -> date | None:
        return self._db.scalar(
            select(func.max(PriceCandle.bar_date)).where(
                PriceCandle.stock_code == code, PriceCandle.timeframe == tf
            )
        )

    def upsert_periodic(self, code: str, tf: Timeframe, candles: list[CandleInput]) -> int:
        """봉들을 단일 다중행 INSERT ... ON CONFLICT 로 upsert. 반영 건수 반환(빈 입력=0).

        같은 bar_date 중복은 다중행 ON CONFLICT 가 21000("cannot affect row a second time")로
        실패하므로 날짜별 마지막 값만 남긴다.
        """
        if not candles:
            return 0
        by_date: dict = {}
        for c in candles:
            by_date[c.ts.date()] = c
        rows = [
            {
                "stock_code": code,
                "timeframe": tf,
                "bar_date": d,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "foreign_ratio": getattr(c, "foreign_ratio", None),
            }
            for d, c in by_date.items()
        ]
        stmt = insert(PriceCandle).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "foreign_ratio": stmt.excluded.foreign_ratio,
            },
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(rows)
