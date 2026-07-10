"""범용 동기화 TTL 헬퍼 — 외부 스크랩을 DB 우선 + 만료 시에만 갱신하도록 게이트한다.

(domain, stock_code) 별 마지막 동기화 시각(SyncState)을 보고, TTL 내면 외부 조회를 건너뛴다.
disclosures/valuation 이 각자 쓰던 sync_state 패턴을 재무·peers 등 다른 도메인에도 재사용.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import SyncState


def is_fresh(db: Session, domain: str, code: str, ttl: timedelta) -> bool:
    """마지막 동기화가 ttl 이내면 True(외부 조회 스킵). 기록 없으면 False."""
    last = db.scalar(
        select(SyncState.synced_at).where(
            SyncState.domain == domain, SyncState.stock_code == code
        )
    )
    return last is not None and datetime.now(UTC) - last < ttl


def mark(db: Session, domain: str, code: str) -> None:
    """(domain, code) 동기화 시각을 now 로 upsert 한다. 커밋은 호출측."""
    stmt = insert(SyncState).values(domain=domain, stock_code=code)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_sync_state", set_={"synced_at": datetime.now(UTC)}
    )
    db.execute(stmt)
