"""전 종목 봉 적재 배치 — 일봉 2년치 백필.

유니버스 스냅샷의 보통주 전 종목에 대해 네이버(→KIS 폴백) 일봉을 받아 PriceCandle 에
멱등 upsert 한다. 종목당 ~0.1s 라 ~2800종목이면 수 분. 실패 종목은 건너뛰고 계속한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import PriceCandle, PriceCandleIntraday, Timeframe, UniverseSnapshot
from app.services import chart, intraday, kis

logger = logging.getLogger(__name__)

_DAY_RANGE_DAYS = 365 * 2 + 10  # 2년치
_INTRADAY_TRADING_DAYS = 10  # 2주 ≈ 거래일 10일


def _universe_codes(db: Session) -> list[str]:
    """최신 스냅샷의 보통주 종목코드(ETF/ETN·우선주 제외)."""
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    if as_of is None:
        return []
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
        ).all()
    )


def _upsert(db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandle).values(
            stock_code=code, timeframe=tf, bar_date=c.ts.date(),
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            foreign_ratio=c.foreign_ratio,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle",
            set_={"open": stmt.excluded.open, "high": stmt.excluded.high,
                  "low": stmt.excluded.low, "close": stmt.excluded.close,
                  "volume": stmt.excluded.volume, "foreign_ratio": stmt.excluded.foreign_ratio},
        )
        db.execute(stmt)


def backfill_daily(db: Session, settings: Settings | None = None) -> dict:
    """전 종목 일봉 2년치를 적재한다. {'stocks': 처리수, 'failed': 실패수}."""
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip daily backfill")
        return {"stocks": 0, "failed": 0}

    session = requests.Session()
    end = datetime.now()
    start = end - timedelta(days=_DAY_RANGE_DAYS)
    done = failed = 0
    for i, code in enumerate(codes, 1):
        try:
            candles = chart.fetch_periodic_with_fallback(settings, code, "day", start, end, session)
            if candles:
                _upsert(db, code, Timeframe.DAY, candles)
                db.commit()  # 종목 단위 커밋 — 중간 중단해도 앞선 종목 보존
                done += 1
            else:
                failed += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("daily backfill failed for %s: %s", code, e)
        if i % 200 == 0:
            logger.info("daily backfill %d/%d (ok=%d fail=%d)", i, len(codes), done, failed)

    logger.info("daily backfill done: %d stocks, %d failed", done, failed)
    return {"stocks": done, "failed": failed}


def _recent_trading_days(db: Session, n: int) -> list[str]:
    """적재된 일봉의 최근 n 거래일(YYYYMMDD). 삼성전자(005930) 기준 — 장 열린 날 정확."""
    rows = db.scalars(
        select(PriceCandle.bar_date)
        .where(PriceCandle.stock_code == "005930", PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(n)
    ).all()
    return [d.strftime("%Y%m%d") for d in reversed(rows)]


def _intraday_loaded_codes(db: Session) -> set[str]:
    """이미 30분봉이 적재된 종목코드 집합 — 중단 후 재개 시 재조회 방지."""
    return set(db.scalars(select(PriceCandleIntraday.stock_code).distinct()).all())


def backfill_intraday(db: Session, settings: Settings | None = None) -> dict:
    """전 종목 30분봉 2주치(≈10거래일)를 KIS 분봉으로 적재한다. 매우 무겁다(종목당 40+콜).

    이미 적재된 종목은 건너뛰어(재개 가능) 중단 후 재실행해도 KIS 콜을 낭비하지 않는다.
    {'stocks': 처리수, 'failed': 실패수, 'skipped': 기적재수, 'days': 거래일수}.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    days = _recent_trading_days(db, _INTRADAY_TRADING_DAYS)
    if not codes or not days:
        logger.warning("no codes/days; skip intraday backfill (일봉 먼저 적재 필요)")
        return {"stocks": 0, "failed": 0, "skipped": 0, "days": len(days)}

    loaded = _intraday_loaded_codes(db)
    pending = [c for c in codes if c not in loaded]
    session = requests.Session()
    done = failed = 0
    for i, code in enumerate(pending, 1):
        try:
            bars = kis.fetch_intraday_30min(settings, code, days, session)
            if bars:
                intraday.upsert_intraday(db, code, bars)
                done += 1
            else:
                failed += 1
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("intraday backfill failed for %s: %s", code, e)
        if i % 100 == 0:
            logger.info(
                "intraday backfill %d/%d (ok=%d fail=%d, skipped=%d)",
                i, len(pending), done, failed, len(loaded),
            )

    logger.info(
        "intraday backfill done: %d stocks, %d failed, %d skipped, %d days",
        done, failed, len(loaded), len(days),
    )
    return {"stocks": done, "failed": failed, "skipped": len(loaded), "days": len(days)}
