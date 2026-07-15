"""경제 캘린더 — 수집 멱등 upsert + 과거/미래 분리 조회 단위 테스트.

FRED/LLM 외부 호출은 배제하고 도메인 로직(멱등·구간·과거미래 정렬·LLM 텍스트 보존)만 검증한다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, CalendarEvent
from app.services import calendar_ingest


@pytest.fixture
def db(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[CalendarEvent.__table__])
    # FRED 는 키 없음 → 빈 리스트. 고정일정만으로 수집 로직을 검증(외부 호출 없음).
    monkeypatch.setattr(calendar_ingest.fred, "fetch_events", lambda *a, **k: [])
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _add(db, *, day, title, region="US", kind="macro", source="manual", **extra):
    db.add(CalendarEvent(
        event_date=day, title=title, region=region, kind=kind,
        source=source, source_key=f"{day.isoformat()}:{title}", **extra,
    ))
    db.commit()


def test_list_events_splits_past_and_future(db):
    today = date(2026, 7, 15)
    _add(db, day=today - timedelta(days=3), title="지난 CPI", actual="332.5")
    _add(db, day=today, title="오늘 이벤트")  # 오늘은 과거로 포함(<=)
    _add(db, day=today + timedelta(days=10), title="다가올 FOMC", kind="fomc")

    view = calendar_ingest.list_events(db, today=today)
    assert view.as_of == today
    assert [e.title for e in view.past] == ["오늘 이벤트", "지난 CPI"]  # 최신순
    assert [e.title for e in view.upcoming] == ["다가올 FOMC"]
    # is_past 플래그가 올바르게 채워진다.
    assert all(e.is_past for e in view.past)
    assert all(not e.is_past for e in view.upcoming)


def test_list_events_filters_region_and_kind(db):
    today = date(2026, 7, 15)
    _add(db, day=today + timedelta(days=5), title="미국 CPI", region="US", kind="macro")
    _add(db, day=today + timedelta(days=6), title="금통위", region="KR", kind="macro")
    _add(db, day=today + timedelta(days=7), title="FOMC", region="US", kind="fomc")

    kr = calendar_ingest.list_events(db, today=today, region="KR")
    assert {e.title for e in kr.upcoming} == {"금통위"}
    fomc = calendar_ingest.list_events(db, today=today, kind="fomc")
    assert {e.title for e in fomc.upcoming} == {"FOMC"}


def test_list_events_respects_window(db):
    today = date(2026, 7, 15)
    _add(db, day=today + timedelta(days=200), title="먼 미래")  # future_days 밖
    _add(db, day=today - timedelta(days=200), title="먼 과거")  # past_days 밖
    _add(db, day=today + timedelta(days=5), title="창 안")
    view = calendar_ingest.list_events(db, today=today, past_days=60, future_days=90)
    titles = {e.title for e in view.past + view.upcoming}
    assert titles == {"창 안"}


def test_ingest_is_idempotent_and_preserves_llm_text(db, monkeypatch):
    # 고정일정 1건을 두 번 수집해도 1행(멱등). 사이에 넣은 LLM 텍스트는 재수집이 안 덮어쓴다.
    today = date(2026, 7, 15)
    fe = calendar_ingest.fixed_events.FixedEvent(today + timedelta(days=3), "테스트 FOMC", "US", "fomc", 3)
    monkeypatch.setattr(calendar_ingest.fixed_events, "fetch_fixed", lambda s, e: [fe])

    calendar_ingest.ingest_calendar(db, today=today)
    row = db.execute(select(CalendarEvent)).scalars().one()
    row.expectation_text = "시장은 동결 기대"  # LLM 이 채운 텍스트
    db.commit()

    calendar_ingest.ingest_calendar(db, today=today)  # 재수집
    rows = db.execute(select(CalendarEvent)).scalars().all()
    assert len(rows) == 1  # 멱등: 중복 안 생김
    assert rows[0].expectation_text == "시장은 동결 기대"  # LLM 텍스트 보존
