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
    job.hitl_input = ""  # HITL 건너뜀(검증 없이 밸류에이션 진행)

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
    job.hitl_input = ""  # HITL 건너뜀

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


# ── HITL(밸류에이션 직전 일시정지·재개) ─────────────────────────────────
def test_run_job_pauses_before_valuation_when_no_input():
    # thesis 까지 돌고 밸류에이션 직전에서 인풋 없으면 paused 로 멈추고 valuation 은 실행 안 됨.
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = rep.redflags_json = rep.business_json = rep.thesis_json = None
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 20, "093320", 0
    job.hitl_input = None  # 아직 인풋 없음

    db = MagicMock()
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o, "_get_or_create_report", return_value=rep), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o.hitl, "build_prompt", return_value="인풋 주세요"), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert "valuation" not in ran  # 밸류에이션 직전 멈춤
    assert ran == ["overview", "redflags", "business", "thesis"]
    assert job.status == "paused" and job.hitl_pending is True
    assert job.hitl_prompt == "인풋 주세요"


def test_run_job_verifies_input_and_resumes():
    # 인풋을 받으면 검증(verify_input)해 rep.hitl_json 에 저장하고 밸류에이션까지 진행.
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = {"per": 10}
    rep.redflags_json = {"severity": "양호"}
    rep.business_json = {"moat": "x"}
    rep.thesis_json = {"thesis": "t"}
    rep.hitl_json = None
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 21, "093320", 4  # thesis 까지 완료
    job.hitl_input = "신규 대형 수주 임박"

    verdicts = {"claims": [{"verdict": "가능성", "probability": 0.4}]}
    db = MagicMock()
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o, "_get_or_create_report", return_value=rep), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o.hitl, "verify_input", return_value=verdicts), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert ran == ["valuation"]  # thesis 까지 스킵, 밸류에이션만 실행
    assert rep.hitl_json == verdicts  # 검증 결과 저장
    assert job.status == "done"


def test_run_job_fails_when_hitl_verify_errors():
    # HITL 검증이 실패(LLM 타임아웃 등 에러 마커)하면 인풋을 조용히 버리지 않고 job 을 실패시킨다
    # (사용자 재시도 유도). hitl_json 에 에러 마커를 남기지 않아 재개 시 다시 검증한다.
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = {"per": 10}
    rep.redflags_json = {"severity": "양호"}
    rep.business_json = {"moat": "x"}
    rep.thesis_json = {"thesis": "t"}
    rep.hitl_json = None
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 22, "093320", 4
    job.hitl_input = "가비아 JV 코어허브 100MW IDC"

    err = {"_error": "LLM 실패: Read timed out", "_partial": True}
    db = MagicMock()
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o, "_get_or_create_report", return_value=rep), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o.hitl, "verify_input", return_value=err), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert "valuation" not in ran  # 밸류에이션 진행 안 함
    assert job.status == "failed"  # 조용히 진행하지 않고 실패
    assert rep.hitl_json is None  # 에러 마커를 남기지 않아 재개 시 재검증


def test_run_job_skips_verify_on_blank_input():
    # 공백 인풋(건너뜀)이면 verify_input 을 호출하지 않고 바로 밸류에이션 진행.
    from unittest.mock import MagicMock, patch

    from app.services.deepdive import orchestrator as o

    ran, fake, Report = _run_job_stages()
    rep = Report(stock_code="093320")
    rep.overview_json = {"per": 10}
    rep.redflags_json = {"severity": "양호"}
    rep.business_json = {"moat": "x"}
    rep.thesis_json = {"thesis": "t"}
    rep.hitl_json = None
    job = MagicMock()
    job.id, job.stock_code, job.current_stage = 22, "093320", 4
    job.hitl_input = "   "  # 공백 = 건너뜀

    called = {"verify": False}

    def _boom(*a, **k):
        called["verify"] = True
        return {}

    db = MagicMock()
    with patch.object(o, "get_llm", return_value=MagicMock()), \
         patch.object(o.tools, "resolve_corp_code", return_value="c"), \
         patch.object(o, "_get_or_create_report", return_value=rep), \
         patch.object(o.stages, "STAGES", fake), \
         patch.object(o.hitl, "verify_input", _boom), \
         patch.object(o, "_finalize", lambda *a: None):
        o.run_job(db, job, MagicMock())
    assert ran == ["valuation"]
    assert called["verify"] is False  # 공백이면 검증 스킵
    assert rep.hitl_json is None


def test_submit_hitl_resumes_paused_job(db):
    from app.services.deepdive import orchestrator

    _job(db, status="paused", hitl_pending=True, hitl_prompt="?")
    job = orchestrator.submit_hitl(db, "000000", "신규 수주")
    assert job is not None
    assert job.status == "pending" and job.hitl_pending is False
    assert job.hitl_input == "신규 수주"


def test_submit_hitl_none_when_not_paused(db):
    from app.services.deepdive import orchestrator

    _job(db, status="running")
    assert orchestrator.submit_hitl(db, "000000", "x") is None
