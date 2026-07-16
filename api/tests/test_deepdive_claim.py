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


# ── run_job 재개(중단 지점부터) ───────────────────────────────────────────
def _run_job_stages():
    from app.db.models import DeepDiveReport

    ran = []

    def mk(name):
        def f(llm, model, ctx, prior):
            ran.append(name)
            return {name: "v"}
        return f

    fake = [("overview", mk("overview")), ("redflags", mk("redflags")),
            ("business", mk("business")), ("thesis", mk("thesis")), ("valuation", mk("valuation"))]
    return ran, fake, DeepDiveReport


def test_run_job_resumes_from_interrupted_stage(monkeypatch):
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = {"per": 10}
    rep.redflags_json = {"severity": "양호"}
    rep.business_json = {"moat": "x"}
    rep.thesis_json = rep.valuation_json = None
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 10, "093320", 3  # 3단계까지 완료 후 중단

    db = MagicMock()
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o, "_get_or_create_report", return_value=rep), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert ran == ["thesis", "valuation"]  # 완료 단계 스킵, 중단 지점부터
    assert rep.overview_json == {"per": 10}  # 기존 결과 보존


def test_run_job_fresh_runs_all_stages(monkeypatch):
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = {"old": 1}
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 11, "093320", 0  # fresh

    db = MagicMock()
    db.scalar.return_value = rep
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert ran == ["overview", "redflags", "business", "thesis", "valuation"]
    assert rep.overview_json == {"overview": "v"}  # fresh 는 이전 잔재 덮어씀


def test_stage_error_marker_reruns_on_resume(monkeypatch):
    # 재개 시 이전 단계가 에러 마커면(불완전) 재실행 대상.
    from app.services.deepdive import orchestrator as o

    assert o._is_stage_error({"_error": "LLM 실패"}) is True
    assert o._is_stage_error({"_note": "비정형"}) is True
    assert o._is_stage_error({"per": 10}) is False
