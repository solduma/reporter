"""지수·환율 시세 시계열 적재/조회 — 대시보드 소스를 DB로.

us_market.fetch_* 는 120s 인메모리 캐시만 있어 워커 재시작 시 소실되고 시계열이 남지 않는다.
이 서비스는 스냅샷을 market_quote 에 쌓아(최근값 조회 + 이력 보존), 대시보드가 DB 우선으로
읽게 한다. 스냅샷은 조회 시(신선하지 않으면) 또는 배치에서 적재한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import MarketQuote
from reporter import us_market

logger = logging.getLogger(__name__)

# 스냅샷 주기 — 시세는 자주 바뀌지만 시계열 밀도는 이 정도면 충분(대시보드용). 분 단위로 버킷팅.
_SNAPSHOT_TTL = timedelta(minutes=5)


def _fetchers():
    return (("us", us_market.fetch_us_indices), ("kr", us_market.fetch_kr_indices), ("fx", us_market.fetch_exchange_rates))


def snapshot_quotes(db: Session) -> int:
    """지수·환율을 조회해 market_quote 에 스냅샷 저장한다. 저장 건수 반환.

    같은 (name, ts) 는 upsert. ts 는 분 단위로 버킷팅해 과도한 행 증가를 막는다.
    """
    # 분 버킷 타임스탬프(초·마이크로초 절삭).
    now = datetime.now(UTC)
    bucket = now.replace(second=0, microsecond=0)
    saved = 0
    for kind, fetch in _fetchers():
        for q in fetch():
            stmt = insert(MarketQuote).values(
                name=q.name, kind=kind, ts=bucket,
                close=q.close, change=q.change, change_ratio=q.change_ratio, rising=q.rising,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_market_quote",
                set_={
                    "close": stmt.excluded.close, "change": stmt.excluded.change,
                    "change_ratio": stmt.excluded.change_ratio, "rising": stmt.excluded.rising,
                    "kind": stmt.excluded.kind,
                },
            )
            db.execute(stmt)
            saved += 1
    db.commit()
    return saved


def is_stale(db: Session) -> bool:
    """마지막 스냅샷이 TTL 보다 오래됐으면 True."""
    last = db.scalar(select(func.max(MarketQuote.ts)))
    return last is None or datetime.now(UTC) - last >= _SNAPSHOT_TTL


def latest_quotes(db: Session, kind: str) -> list[MarketQuote]:
    """해당 kind(us|kr|fx)의 최신 스냅샷 시각의 시세들. 없으면 빈 리스트."""
    last = db.scalar(select(func.max(MarketQuote.ts)).where(MarketQuote.kind == kind))
    if last is None:
        return []
    return list(
        db.scalars(
            select(MarketQuote).where(MarketQuote.kind == kind, MarketQuote.ts == last)
        ).all()
    )
