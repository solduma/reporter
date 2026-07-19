"""시장 위험프리미엄(ERP) 수집 — Damodaran 국가 ERP 를 MarketPremium 으로 멱등 upsert.

밸류에이션(CAPM COE·factor model)이 고정상수(MARKET_PREMIUM) 대신 실측 ERP 를 쓰도록 월 1회
배치로 적재한다. 파싱 실패 시 아무것도 안 하고 상수 폴백에 맡긴다(graceful degrade). (source, as_of_date) 멱등.
"""

from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import damodaran
from app.db.models import MarketPremium

logger = logging.getLogger(__name__)

_SOURCE = "damodaran_kr_erp"


def ingest_erp(db: Session, today=None) -> dict:
    """Damodaran Korea ERP 를 as_of(오늘) 로 upsert. 실패 시 skip."""
    erp = damodaran.fetch_country_erp("Korea")
    if erp is None:
        return {"inserted": 0, "skipped": "fetch_failed"}
    from datetime import date

    as_of = today or date.today()
    stmt = insert(MarketPremium).values(source=_SOURCE, as_of_date=as_of, erp=erp.erp)
    stmt = stmt.on_conflict_do_update(constraint="uq_market_premium", set_={"erp": erp.erp})
    db.execute(stmt)
    db.commit()
    return {"inserted": 1, "erp": erp.erp}


def latest_erp(db: Session) -> float | None:
    """최신 ERP(소수, 예 0.0487). 없으면 None(호출측이 상수 폴백)."""
    row = db.scalars(
        select(MarketPremium)
        .where(MarketPremium.source == _SOURCE)
        .order_by(desc(MarketPremium.as_of_date))
        .limit(1)
    ).first()
    return row.erp if row else None
