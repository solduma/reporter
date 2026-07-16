"""딥다이브 큐 claim_next — pending 우선 + 좀비 running(배포로 고아) 회수."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DeepDiveJob
from app.services.deepdive import orchestrator


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DeepDiveJob.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _job(db, **kw):
    kw.setdefault("status", "pending")
    j = DeepDiveJob(stock_code="000000", **kw)
    db.add(j)
    db.commit()
    return j


def test_pending_claimed_first(db):
    _job(db, status="running", started_at=datetime.now(UTC) - timedelta(hours=2))  # 좀비 있어도
    p = _job(db, status="pending")
    claimed = orchestrator.claim_next(db)
    assert claimed.id == p.id  # pending 우선


def test_stale_running_reclaimed(db):
    old = _job(db, status="running", started_at=datetime.now(UTC) - timedelta(minutes=40))
    claimed = orchestrator.claim_next(db)
    assert claimed.id == old.id
    assert claimed.status == "pending"  # 회수 시 pending 으로 리셋
    assert claimed.current_stage == 0 and claimed.progress == 0


def test_running_with_null_started_at_reclaimed(db):
    # started_at 이 NULL 인 running 도 좀비로 회수(비정상 상태).
    j = _job(db, status="running", started_at=None)
    claimed = orchestrator.claim_next(db)
    assert claimed.id == j.id and claimed.status == "pending"


def test_fresh_running_not_reclaimed(db):
    # 방금 시작한 running(정상 진행 중)은 회수하지 않는다.
    _job(db, status="running", started_at=datetime.now(UTC) - timedelta(minutes=5))
    assert orchestrator.claim_next(db) is None


def test_none_when_empty(db):
    assert orchestrator.claim_next(db) is None
