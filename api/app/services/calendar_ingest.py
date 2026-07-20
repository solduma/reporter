"""경제/실적 캘린더 수집 — FRED(미국 매크로) + 수동 고정일정을 CalendarEvent 로 멱등 upsert.

수치·날짜만 적재한다. LLM 영향/기대치 텍스트는 calendar_llm 이 별도로 채운다(수집이 그 텍스트를
덮어쓰지 않도록 upsert set_ 에서 impact/expectation/inputs_hash 제외). FRED 키 미설정 시 고정
일정만 적재(graceful degrade). (source, source_key) 로 멱등.

사후 업데이트: fixed_events 의 금통위(기준금리)는 ECOS 기준금리 통계로 actual 을 채운다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import ecos, fixed_events, fred
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
            db,
            source="fred",
            source_key=f"{ev.release_id}:{ev.event_date.isoformat()}",
            values={
                "event_date": ev.event_date,
                "region": "US",
                "kind": "macro",
                "title": ev.title,
                "importance": ev.importance,
                "actual": ev.latest_value,
                "previous": ev.prev_value,
            },
        )
        n_fred += 1

    n_fixed = 0
    for fe in fixed_events.fetch_fixed(start, end):
        _upsert(
            db,
            source="manual",
            source_key=f"{fe.event_date.isoformat()}:{fe.title}",
            values={
                "event_date": fe.event_date,
                "region": fe.region,
                "kind": fe.kind,
                "title": fe.title,
                "importance": fe.importance,
            },
        )
        n_fixed += 1

    db.commit()
    logger.info("calendar ingest: fred=%d fixed=%d", n_fred, n_fixed)

    # ECOS 기준금리로 지난 한국 금통위 이벤트 actual 을 사후 채운다(파이프라인 일원).
    n_ecos = update_past_fixed_events(db, settings, today)

    return {"fred": n_fred, "fixed": n_fixed, "ecos": n_ecos}


def _to_out(ev: CalendarEvent, today: date) -> CalendarEventOut:
    return CalendarEventOut(
        event_date=ev.event_date,
        region=ev.region,
        kind=ev.kind,
        title=ev.title,
        importance=ev.importance,
        is_past=ev.event_date <= today,
        actual=ev.actual,
        previous=ev.previous,
        consensus=ev.consensus,
        unit=ev.unit,
        impact_text=ev.impact_text,
        impact_direction=ev.impact_direction,
        expectation_text=ev.expectation_text,
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
    past = sorted(
        (e for e in rows if e.event_date <= today), key=lambda e: e.event_date, reverse=True
    )
    upcoming = sorted((e for e in rows if e.event_date > today), key=lambda e: e.event_date)
    return CalendarView(
        as_of=today,
        past=[_to_out(e, today) for e in past],
        upcoming=[_to_out(e, today) for e in upcoming],
    )


def update_past_fixed_events(
    db: Session,
    settings: Settings | None = None,
    today: date | None = None,
) -> int:
    """지난 고정 이벤트(금통위 기준금리)의 actual 값을 ECOS 기준금리 통계로 채운다.

    fixed_events 중 region='KR', kind='macro', title='한국은행 금통위 (기준금리)' 이면서
    actual 이 NULL 인 과거 이벤트를 찾아, ECOS 기준금리 월별 통계에서 해당 월의 금리를 actual 에
    기록한다. 업데이트한 건수 반환.
    """
    settings = settings or get_settings()
    today = today or date.today()
    key = settings.ecos_api_key
    if not key:
        return 0

    # actual 이 없는 과거 금통위 이벤트 조회
    rows = (
        db.execute(
            select(CalendarEvent).where(
                and_(
                    CalendarEvent.region == "KR",
                    CalendarEvent.kind == "macro",
                    CalendarEvent.title == "한국은행 금통위 (기준금리)",
                    CalendarEvent.event_date <= today,
                    CalendarEvent.actual.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    # ECOS 기준금리 조회(가장 오래된 이벤트 1년 전 ~ 오늘)
    min_date = min(e.event_date for e in rows) - timedelta(days=365)
    rates = ecos.fetch_base_rate(key, min_date, today)
    if not rates:
        logger.warning("ECOS base_rate returned empty — cannot update past events")
        return 0

    # 월별 기준금리 맵: (year, month) → rate
    rate_by_ym: dict[tuple[int, int], float] = {}
    for r in rates:
        rate_by_ym[(r.rate_date.year, r.rate_date.month)] = r.rate

    updated = 0
    for ev in rows:
        ym = (ev.event_date.year, ev.event_date.month)
        rate = rate_by_ym.get(ym)
        if rate is None:
            # 해당 월 데이터가 없으면 직전 월 기준금리로 폴백
            prev_ym = (
                (ev.event_date.year, ev.event_date.month - 1)
                if ev.event_date.month > 1
                else (ev.event_date.year - 1, 12)
            )
            rate = rate_by_ym.get(prev_ym)
        if rate is not None:
            ev.actual = f"{rate:.2f}%"
            ev.previous = _find_prev_rate(rate_by_ym, ym)
            updated += 1

    if updated:
        db.commit()
        logger.info(
            "calendar post-update: filled %d past fixed events with ECOS base rate", updated
        )
    return updated


def _find_prev_rate(
    rate_by_ym: dict[tuple[int, int], float],
    ym: tuple[int, int],
) -> str | None:
    """ym 직전 월의 기준금리를 문자열로 반환. 없으면 None."""
    year, month = ym
    for _ in range(12):
        month -= 1
        if month < 1:
            month = 12
            year -= 1
        rate = rate_by_ym.get((year, month))
        if rate is not None:
            return f"{rate:.2f}%"
    return None
