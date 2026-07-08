"""30분봉 누적 적재. 네이버 분봉 보존기간이 짧아 매 거래일 cron 으로 2주 윈도우를 쌓는다.

라우터(조회 시 cache-aside)와 스케줄러(주기 누적)가 공유한다. upsert 라 멱등하다.
"""

from __future__ import annotations

import logging

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import PriceCandleIntraday, Report
from app.services import chart

logger = logging.getLogger(__name__)


def upsert_intraday(db: Session, code: str, candles: list[chart.Candle]) -> int:
    """30분봉을 upsert 한다. 반영한 봉 수를 반환한다."""
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
    if candles:
        db.commit()
    return len(candles)


def tracked_stock_codes(db: Session) -> list[str]:
    """추적 대상 종목: reports 에 stock_code 가 있는 종목(당일 리포트 대상)."""
    rows = db.scalars(
        select(Report.stock_code).where(Report.stock_code.is_not(None)).distinct()
    ).all()
    return [c for c in rows if c]


def accumulate_intraday(db: Session) -> int:
    """추적 종목들의 30분봉을 수집·누적한다. 반영한 종목 수를 반환한다."""
    session = requests.Session()
    codes = tracked_stock_codes(db)
    touched = 0
    for code in codes:
        try:
            candles = chart.fetch_intraday_30min(code, session)
            if candles:
                upsert_intraday(db, code, candles)
                touched += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            logger.warning("intraday accumulate failed for %s: %s", code, e)
    logger.info("intraday accumulated for %d/%d codes", touched, len(codes))
    return touched
