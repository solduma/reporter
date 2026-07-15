"""경제/실적 캘린더 수집 — FRED(미국 매크로) + 수동 고정일정을 CalendarEvent 로 멱등 upsert.

수치·날짜만 적재한다. LLM 영향/기대치 텍스트는 calendar_llm 이 별도로 채운다(수집이 그 텍스트를
덮어쓰지 않도록 upsert set_ 에서 impact/expectation/inputs_hash 제외). FRED 키 미설정 시 고정
일정만 적재(graceful degrade). (source, source_key) 로 멱등.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import fixed_events, fred
from app.config import Settings, get_settings
from app.db.models import CalendarEvent
from app.schemas import CalendarEventOut, CalendarView

logger = logging.getLogger(__name__)

# 수집 창(기본): 과거 N일 ~ 미래 M일. 지난 이벤트는 결과·영향, 미래는 기대치 표시용.
_PAST_DAYS = 60
_FUTURE_DAYS = 90


def _upsert(db: Session, *, source: str, source_key: str, values: dict) -> None:
    """CalendarEvent 멱등 upsert. LLM 텍스트 컬럼은 갱신 대상에서 제외(수집이 안 덮어씀)."""
    stmt = insert(CalendarEvent).values(source=source, source_key=source_key, **values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_calendar_event",
        set_={k: getattr(stmt.excluded, k) for k in values},  # 수치·날짜·제목만 갱신
    )
    db.execute(stmt)


def ingest_calendar(
    db: Session,
    settings: Settings | None = None,
    today: date | None = None,
) -> dict:
    """FRED + 고정일정을 수집·upsert. 적재 건수 dict 반환."""
    settings = settings or get_settings()
    today = today or date.today()
    start, end = today - timedelta(days=_PAST_DAYS), today + timedelta(days=_FUTURE_DAYS)

    n_fred = 0
    for ev in fred.fetch_events(settings.fred_api_key, start, end):
        _upsert(
            db, source="fred", source_key=f"{ev.release_id}:{ev.event_date.isoformat()}",
            values={
                "event_date": ev.event_date, "region": "US", "kind": "macro",
                "title": ev.title, "importance": ev.importance,
                "actual": ev.latest_value, "previous": ev.prev_value,
            },
        )
        n_fred += 1

    n_fixed = 0
    for fe in fixed_events.fetch_fixed(start, end):
        _upsert(
            db, source="manual", source_key=f"{fe.event_date.isoformat()}:{fe.title}",
            values={
                "event_date": fe.event_date, "region": fe.region, "kind": fe.kind,
                "title": fe.title, "importance": fe.importance,
            },
        )
        n_fixed += 1

    db.commit()
    logger.info("calendar ingest: fred=%d fixed=%d", n_fred, n_fixed)
    return {"fred": n_fred, "fixed": n_fixed}


def _to_out(ev: CalendarEvent, today: date) -> CalendarEventOut:
    return CalendarEventOut(
        event_date=ev.event_date, region=ev.region, kind=ev.kind, title=ev.title,
        importance=ev.importance, is_past=ev.event_date <= today,
        actual=ev.actual, previous=ev.previous, consensus=ev.consensus, unit=ev.unit,
        impact_text=ev.impact_text, expectation_text=ev.expectation_text,
    )


def list_events(
    db: Session,
    today: date | None = None,
    region: str | None = None,
    kind: str | None = None,
    past_days: int = _PAST_DAYS,
    future_days: int = _FUTURE_DAYS,
) -> CalendarView:
    """조회 구간의 이벤트를 과거[최신순]/미래[임박순] read-model 로 반환. region/kind 필터."""
    today = today or date.today()
    start, end = today - timedelta(days=past_days), today + timedelta(days=future_days)
    conds = [CalendarEvent.event_date >= start, CalendarEvent.event_date <= end]
    if region:
        conds.append(CalendarEvent.region == region)
    if kind:
        conds.append(CalendarEvent.kind == kind)
    rows = db.execute(select(CalendarEvent).where(*conds)).scalars().all()
    past = sorted((e for e in rows if e.event_date <= today), key=lambda e: e.event_date, reverse=True)
    upcoming = sorted((e for e in rows if e.event_date > today), key=lambda e: e.event_date)
    return CalendarView(
        as_of=today,
        past=[_to_out(e, today) for e in past],
        upcoming=[_to_out(e, today) for e in upcoming],
    )
