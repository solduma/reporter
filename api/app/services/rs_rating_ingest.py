"""IBD RS Rating 배치 — 전 유니버스의 가격 모멘텀 강도지수를 백분위(1~99)로 적재.

price_candles(백필 완료)만 읽어 계산하므로 외부 fetch 가 없다. 종목별 강도지수를 구한 뒤
전 종목 횡단면 백분위로 rs_rating 을 매겨 universe_snapshot 에 UPDATE 한다(야간 배치).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PriceCandle, Timeframe, UniverseSnapshot
from app.domain import rs_rating
from app.services import universe_ingest

logger = logging.getLogger(__name__)

# 강도지수 계산에 필요한 최소 봉 수(1년치)보다 여유롭게 최근분만 읽어 메모리·시간 절약.
_LOOKBACK_BARS = 300


def _universe_codes(db: Session, snap_date: date) -> list[str]:
    stmt = select(UniverseSnapshot.stock_code).where(
        UniverseSnapshot.snapshot_date == snap_date,
        UniverseSnapshot.stock_type == "stock",
        UniverseSnapshot.market_cap.is_not(None),
    )
    return list(db.scalars(stmt).all())


def _recent_closes(db: Session, code: str) -> list[float]:
    """종목의 최근 일봉 종가(오름차순). 강도지수 계산에 필요한 만큼만 읽는다."""
    rows = db.scalars(
        select(PriceCandle.close)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(_LOOKBACK_BARS)
    ).all()
    return list(reversed(rows))  # desc 로 읽었으니 뒤집어 오름차순


def run_rs_rating_batch(db: Session) -> dict:
    """전 유니버스의 RS Rating(1~99)을 계산·적재한다. 처리 종목 수를 반환한다."""
    snap_date = universe_ingest.latest_snapshot_date(db)
    if not snap_date:
        return {"rated": 0, "total": 0}

    codes = _universe_codes(db, snap_date)
    # 1) 종목별 강도지수 계산(외부 fetch 없음, price_candles read).
    factors: dict[str, float] = {}
    for code in codes:
        sf = rs_rating.strength_factor(_recent_closes(db, code))
        if sf is not None:
            factors[code] = sf

    # 2) 전 종목 횡단면 백분위 → 1~99.
    sorted_factors = sorted(factors.values())
    rated = 0
    for code, sf in factors.items():
        rating = rs_rating.to_rating(sf, sorted_factors)
        if rating is not None:
            db.execute(
                UniverseSnapshot.__table__.update()
                .where(
                    UniverseSnapshot.snapshot_date == snap_date,
                    UniverseSnapshot.stock_code == code,
                )
                .values(rs_rating=rating)
            )
            rated += 1
    db.commit()
    logger.info("rs rating batch: %d/%d rated (%s)", rated, len(codes), snap_date)
    return {"rated": rated, "total": len(codes)}
