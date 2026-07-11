"""US 유니버스 스냅샷 적재 — 시드 종목의 네이버 시세를 오늘 날짜로 upsert(스크리너 소스).

S&P500(+보충) ~600종목을 종목당 네이버 1콜로 시총·PER/PBR·거래대금·52주를 받아 us_universe 에
쌓는다. 종목 간 간격을 둬 네이버 연타 차단을 피한다(KR growth_ingest 패턴). 야간 배치.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import us_universe as source
from app.db.models import PriceCandle, Timeframe, UsUniverse

logger = logging.getLogger(__name__)

_STOCK_INTERVAL_S = 0.15  # 종목 간 간격(네이버 무인증 연타 차단 회피)


def _momentum_3m(db: Session, naver_symbol: str) -> float | None:
    """저장된 US 일봉(candle_service 가 조회 시 적재)에서 3개월(약 63거래일) 수익률%. 없으면 None."""
    rows = list(
        db.scalars(
            select(PriceCandle.close)
            .where(PriceCandle.stock_code == naver_symbol, PriceCandle.timeframe == Timeframe.DAY)
            .order_by(PriceCandle.bar_date.desc())
            .limit(64)
        ).all()
    )
    if len(rows) < 64 or not rows[-1]:
        return None
    last, past = rows[0], rows[-1]
    return round((last / past - 1) * 100, 1) if past else None


def latest_snapshot_date(db: Session) -> date | None:
    return db.scalar(select(UsUniverse.snapshot_date).order_by(UsUniverse.snapshot_date.desc()).limit(1))


def snapshot_us_universe(db: Session, snapshot_date: date | None = None) -> dict:
    """시드 종목을 오늘 날짜 us_universe 스냅샷으로 적재. {seeded, saved, skipped} 반환."""
    snapshot_date = snapshot_date or datetime.now(UTC).date()
    session = requests.Session()
    seeds = source.seed_tickers(session)
    saved = skipped = 0
    for ticker, sector in seeds:
        time.sleep(_STOCK_INTERVAL_S)
        row = source.fetch_row(ticker, sector, session)
        if row is None or row.market_cap is None:
            skipped += 1
            continue
        values = {
            "naver_symbol": row.naver_symbol,
            "name": row.name,
            "exchange": row.exchange,
            "sector": row.sector,
            "close_price": row.close_price,
            "change_pct": row.change_pct,
            "market_cap": row.market_cap,
            "trading_value": row.trading_value,
            "per": row.per,
            "pbr": row.pbr,
            "eps": row.eps,
            "high_52w": row.high_52w,
            "low_52w": row.low_52w,
            "momentum_3m": _momentum_3m(db, row.naver_symbol),
        }
        stmt = insert(UsUniverse).values(snapshot_date=snapshot_date, ticker=ticker, **values)
        stmt = stmt.on_conflict_do_update(constraint="uq_us_universe", set_=values)
        db.execute(stmt)
        saved += 1
    db.commit()
    logger.info("us universe snapshot %s: %d seeded, %d saved, %d skipped", snapshot_date, len(seeds), saved, skipped)
    return {"seeded": len(seeds), "saved": saved, "skipped": skipped}
