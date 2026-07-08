"""성장지표 배치 — 스크리너 유니버스(시총 상한 이하)의 재무·모멘텀을 적재.

종목당 main.naver 재무 스크랩(~0.4s) + 3개월 모멘텀(price_candles 없으면 chart API).
무겁기에 야간 배치로 시총 상한 이하 종목만 처리한다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import GrowthMetric as GrowthMetricRow
from app.db.models import UniverseSnapshot
from app.services import chart, growth, quote

logger = logging.getLogger(__name__)

# 배치 대상 시총 상한(원). 스크리너 최대 프리셋(1조)까지만 재무를 채운다.
_MKTCAP_CEILING = 1_000_000_000_000
_MOMENTUM_DAYS = 90


def _latest_snapshot_date(db: Session) -> date | None:
    return db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))


def _momentum_3m(code: str, session: requests.Session) -> float | None:
    """일봉 종가로 3개월 수익률(%)을 계산한다. 데이터 부족 시 None."""
    end = datetime.now()
    candles = chart.fetch_periodic(code, "day", end - timedelta(days=150), end, session)
    if len(candles) < 40:  # 3개월치 거래일(~60)에 못 미치면 신뢰 불가
        return None
    latest = candles[-1].close
    cutoff = end - timedelta(days=_MOMENTUM_DAYS)
    past = next((c.close for c in candles if c.ts >= cutoff), candles[0].close)
    if not past:
        return None
    return round((latest - past) / past * 100, 2)


def run_growth_batch(db: Session, limit: int | None = None) -> dict:
    """유니버스(시총 상한 이하) 종목의 재무·모멘텀을 적재. 처리 종목 수를 반환한다."""
    snap_date = _latest_snapshot_date(db)
    if not snap_date:
        return {"financials": 0, "momentum": 0}

    stmt = (
        select(UniverseSnapshot.stock_code)
        .where(
            UniverseSnapshot.snapshot_date == snap_date,
            UniverseSnapshot.stock_type == "stock",
            UniverseSnapshot.market_cap.is_not(None),
            UniverseSnapshot.market_cap <= _MKTCAP_CEILING,
            UniverseSnapshot.trading_value > 0,
        )
        .order_by(UniverseSnapshot.market_cap)
    )
    if limit:
        stmt = stmt.limit(limit)
    codes = list(db.scalars(stmt).all())

    session = requests.Session()
    fin_count = 0
    for code in codes:
        try:
            _ingest_one(db, code, snap_date, session)
            fin_count += 1
        except Exception as e:  # 종목 하나 실패가 배치를 막지 않도록
            db.rollback()
            logger.warning("growth ingest failed %s: %s", code, e)
    db.commit()
    logger.info("growth batch: %d codes processed", fin_count)
    return {"processed": fin_count, "total": len(codes)}


def _ingest_one(db: Session, code: str, snap_date: date, session: requests.Session) -> None:
    # 1) 재무 → 성장지표
    fins = quote.fetch_financials(code, session)
    metric = growth.compute_growth(code, fins) if fins else None
    if metric:
        stmt = insert(GrowthMetricRow).values(
            stock_code=code,
            period=metric.period,
            revenue_yoy=metric.revenue_yoy,
            op_yoy=metric.op_yoy,
            op_turnaround=metric.op_turnaround,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_growth_stock",
            set_={
                "period": stmt.excluded.period,
                "revenue_yoy": stmt.excluded.revenue_yoy,
                "op_yoy": stmt.excluded.op_yoy,
                "op_turnaround": stmt.excluded.op_turnaround,
                "updated_at": func.now(),  # onupdate 는 on_conflict 에 안 걸려 명시
            },
        )
        db.execute(stmt)

    # 2) 3개월 모멘텀 → universe_snapshot 보강
    mom = _momentum_3m(code, session)
    if mom is not None:
        db.execute(
            UniverseSnapshot.__table__.update()
            .where(
                UniverseSnapshot.snapshot_date == snap_date,
                UniverseSnapshot.stock_code == code,
            )
            .values(momentum_3m=mom)
        )
    db.commit()
