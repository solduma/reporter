"""폴백 이벤트 DB 영속화 sink + 조회. reporter.fallback 의 API 측 백엔드.

reporter.fallback 은 계층상 DB 를 모르므로, API 가 startup 에서 이 모듈의 sink 를 등록한다
(app.main lifespan). 단일 writer 불변식: 폴백 영속화는 API 프로세스만 수행한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FallbackEvent
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def db_sink(key: str, reason: str, detail: str | None, context: dict) -> None:
    """폴백 이벤트 1건을 fallback_event 에 기록한다. 자체 세션을 열고 닫는다.

    reporter.fallback.log_fallback 이 sink 실패를 흡수하지만, 여기서도 커밋 실패 시 rollback 해
    호출측(주로 조회 트랜잭션과 무관한 폴백 경로)에 영향을 주지 않는다.
    """
    db = SessionLocal()
    try:
        db.add(
            FallbackEvent(key=key, reason=reason, detail=detail or "", context=context or {})
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@dataclass
class FallbackRow:
    ts: datetime
    key: str
    reason: str
    detail: str


@dataclass
class FallbackCount:
    key: str
    count: int


def recent_fallbacks(db: Session, limit: int = 50) -> list[FallbackRow]:
    """최근 폴백 이벤트를 최신순으로 반환한다."""
    rows = db.scalars(
        select(FallbackEvent).order_by(FallbackEvent.ts.desc()).limit(limit)
    ).all()
    return [FallbackRow(ts=r.ts, key=r.key, reason=r.reason, detail=r.detail) for r in rows]


def fallback_counts(db: Session, since_hours: int = 24) -> list[FallbackCount]:
    """최근 since_hours 시간 내 key 별 발생 건수(내림차순)."""
    since = func.now() - timedelta(hours=since_hours)
    rows = db.execute(
        select(FallbackEvent.key, func.count().label("n"))
        .where(FallbackEvent.ts >= since)
        .group_by(FallbackEvent.key)
        .order_by(func.count().desc())
    ).all()
    return [FallbackCount(key=k, count=n) for k, n in rows]
