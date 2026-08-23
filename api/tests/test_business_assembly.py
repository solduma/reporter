"""사업 개요 조립 큐 서비스 테스트 — dedup·claim 순서·stale 회수·run_job 상태 전이."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, BusinessAssemblyJob
from app.services import business_assembly as ba


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BusinessAssemblyJob.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _settings():
    s = MagicMock()
    s.insight_model = "m"
    return s


def test_enqueue_dedups_active_job(db):
    j1 = ba.enqueue(db, "005930")
    j2 = ba.enqueue(db, "005930")
    assert j1.id == j2.id
    assert db.query(BusinessAssemblyJob).count() == 1


def test_enqueue_allows_new_job_after_done(db):
    done = ba.enqueue(db, "005930")
    done.status = "done"
    db.commit()
    again = ba.enqueue(db, "005930")
    assert again.id != done.id  # 진행 중이 아니므로 신규 허용


def test_claim_next_fifo(db):
    ba.enqueue(db, "A")
    ba.enqueue(db, "B")
    claimed = ba.claim_next(db)
    assert claimed.stock_code == "A"  # id 오름차순


def test_claim_skips_fresh_running(db):
    job = ba.enqueue(db, "A")
    job.status = "running"
    job.started_at = datetime.now(UTC)
    db.commit()
    assert ba.claim_next(db) is None


def test_claim_reclaims_stale_running(db):
    stale = ba.enqueue(db, "A")
    stale.status = "running"
    stale.started_at = datetime.now(UTC) - timedelta(minutes=31)
    db.commit()
    reclaimed = ba.claim_next(db)
    assert reclaimed is not None and reclaimed.stock_code == "A"
    assert reclaimed.status == "pending"


def test_run_job_success_marks_done_with_progress(db):
    def fake_assemble(dbs, settings, code, progress=None):
        if progress is not None:
            progress(42)
        return {"ok": True}

    job = ba.enqueue(db, "005930")
    with patch.object(ba.business_ingest, "assemble_overview", side_effect=fake_assemble):
        ba.run_job(db, job, _settings())
    assert job.status == "done"
    assert job.progress == 100
    assert job.finished_at is not None


def test_run_job_none_result_marks_failed_with_reason(db):
    job = ba.enqueue(db, "005930")
    with patch.object(ba.business_ingest, "assemble_overview", return_value=None):
        ba.run_job(db, job, _settings())
    assert job.status == "failed"
    assert job.error  # 사유 명시(사업보고서 부재 또는 LLM 미설정)


def test_run_job_assembly_error_records_message(db):
    job = ba.enqueue(db, "005930")
    err = ba.business_ingest.AssemblyError("섹션 생성 과반 미달 3/8")
    with patch.object(ba.business_ingest, "assemble_overview", side_effect=err):
        ba.run_job(db, job, _settings())
    assert job.status == "failed"
    assert "섹션 생성 과반 미달" in (job.error or "")
