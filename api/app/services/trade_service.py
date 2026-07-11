"""관세청 무역통계 서비스 — 조회 시 갱신(customs 어댑터) + upsert + 윈도우 조회.

라우터가 하던 외부 fetch·upsert·조회를 응용 계층으로. 외부 IO 는 adapters.external.customs 위임.
"""

from __future__ import annotations

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import customs
from app.config import get_settings
from app.db.models import TradeStat


def _upsert(db: Session, hs: str, fetched) -> None:
    for m in fetched:
        stmt = insert(TradeStat).values(
            hs_code=hs,
            period=m.period,
            export_usd=m.export_usd,
            import_usd=m.import_usd,
            balance_usd=m.balance_usd,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_trade_stat",
            set_={
                "export_usd": stmt.excluded.export_usd,
                "import_usd": stmt.excluded.import_usd,
                "balance_usd": stmt.excluded.balance_usd,
            },
        )
        db.execute(stmt)
    if fetched:
        db.commit()


def trade_points(db: Session, hs: str, start: str, end: str) -> list[TradeStat]:
    """[start, end](YYYYMM) 구간 무역통계. customs 키 있으면 먼저 조회·upsert 후 반환.

    period 는 'YYYY.MM' 제로패딩이라 문자열 between 으로 대소 판정.
    """
    settings = get_settings()
    if settings.customs_api_key:
        fetched = customs.fetch_trade_by_hs(
            settings.customs_api_key, hs, start, end, requests.Session()
        )
        _upsert(db, hs, fetched)

    start_p, end_p = f"{start[:4]}.{start[4:]}", f"{end[:4]}.{end[4:]}"
    return list(
        db.scalars(
            select(TradeStat)
            .where(TradeStat.hs_code == hs, TradeStat.period.between(start_p, end_p))
            .order_by(TradeStat.period)
        ).all()
    )
