"""기업 분석 페이지용 라우터 — 주가 봉차트 + 종목 요약. (재무/피어/타임라인은 후속 단계)"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import PriceCandle, PriceCandleIntraday, Report, Timeframe
from app.db.session import get_session
from app.schemas import CandlePoint, CompanySummary
from app.services import chart

router = APIRouter(prefix="/api/companies", tags=["companies"])

# tf 별 조회 범위(요구사항): 30m=2주, day=1년(3개월도 프론트에서 슬라이스), month=3년
_RANGE_DAYS = {"day": 400, "month": 365 * 3 + 30}


@router.get("/{code}/summary", response_model=CompanySummary)
def company_summary(code: str, db: Session = Depends(get_session)) -> CompanySummary:
    name = db.scalar(
        select(Report.stock_name)
        .where(Report.stock_code == code, Report.stock_name.is_not(None))
        .order_by(Report.published_date.desc())
        .limit(1)
    )
    return CompanySummary(stock_code=code, stock_name=name)


def _upsert_periodic(db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandle).values(
            stock_code=code,
            timeframe=tf,
            bar_date=c.ts.date(),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            foreign_ratio=c.foreign_ratio,
        )
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
        db.execute(stmt)
    db.commit()


def _upsert_intraday(db: Session, code: str, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandleIntraday).values(
            stock_code=code, bar_ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle_intraday",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
    db.commit()


@router.get("/{code}/candles", response_model=list[CandlePoint])
def company_candles(
    code: str,
    tf: str = Query(default="day", pattern="^(30m|day|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    session = requests.Session()

    if tf == "30m":
        # 매 조회 시 가용 분봉을 리샘플·누적(cache-aside). 누적분과 합쳐 반환.
        fresh = chart.fetch_intraday_30min(code, session)
        if fresh:
            _upsert_intraday(db, code, fresh)
        rows = db.scalars(
            select(PriceCandleIntraday)
            .where(PriceCandleIntraday.stock_code == code)
            .order_by(PriceCandleIntraday.bar_ts)
        ).all()
        return [
            CandlePoint(t=r.bar_ts.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
            for r in rows
        ]

    frame = Timeframe(tf)
    end = datetime.now()
    start = end - timedelta(days=_RANGE_DAYS[tf])
    fresh = chart.fetch_periodic(code, tf, start, end, session)
    if fresh:
        _upsert_periodic(db, code, frame, fresh)
    rows = db.scalars(
        select(PriceCandle)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == frame)
        .order_by(PriceCandle.bar_date)
    ).all()
    return [
        CandlePoint(t=r.bar_date.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
        for r in rows
    ]
