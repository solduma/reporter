"""무위험수익률 수집 — ECOS 국고채 3년/10년을 RiskFreeRate 로 멱등 upsert.

밸류에이션(DCF·factor model)이 고정상수 대신 최신 시장금리를 쓰도록 매일 1회 배치로 적재한다.
ECOS 키 미설정/실패 시 아무것도 안 하고 기존 상수 폴백에 맡긴다(graceful degrade). (maturity, rate_date) 멱등.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import ecos
from app.config import Settings, get_settings
from app.db.models import RiskFreeRate

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 14  # 최근 2주 창(주말·공휴일 결측 대비 넉넉히) 중 최신 관측치 적재.


def ingest_risk_free_rates(
    db: Session, settings: Settings | None = None, today: date | None = None
) -> dict:
    """ECOS 국고채 3년/10년 최근 관측치 upsert. 적재 건수 dict 반환."""
    settings = settings or get_settings()
    key = settings.ecos_api_key
    if not key:
        return {"inserted": 0, "skipped": "no_ecos_key"}
    today = today or date.today()
    start = today - timedelta(days=_LOOKBACK_DAYS)
    count = 0
    for maturity, item in ecos.TREASURY_ITEMS.items():
        for obs in ecos.fetch_market_rate(key, item, maturity, start, today):
            stmt = insert(RiskFreeRate).values(
                maturity=obs.maturity, rate_date=obs.rate_date, rate=obs.rate
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_risk_free_rate", set_={"rate": stmt.excluded.rate}
            )
            db.execute(stmt)
            count += 1
    db.commit()
    return {"inserted": count}


def latest_rate(db: Session, maturity: str) -> float | None:
    """maturity 최신 무위험수익률(소수, 예 0.0324). 없으면 None(호출측이 상수 폴백)."""
    row = db.scalars(
        select(RiskFreeRate)
        .where(RiskFreeRate.maturity == maturity)
        .order_by(desc(RiskFreeRate.rate_date))
        .limit(1)
    ).first()
    if row is None:
        return None
    return row.rate / 100.0  # ECOS 는 연 %(3.24) → 소수(0.0324)
