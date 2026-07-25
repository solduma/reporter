"""사업 리서치 오케스트레이터 테스트.

test_business_ingest.py 패턴 재사용: @compiles(JSONB,"sqlite"), db fixture, _settings,
monkeypatch(get_llm, review_loop, company_service). enqueue dedup, claim_next, run_job,
_merge_research_into_cache, status 전이, LLM 미설정→failed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.models import Base, BusinessOverviewCache, BusinessResearchJob, CorpCodeMap
from app.services import business_ingest, business_research


# SQLite에서 JSONB를 JSON으로 컴파일(@compiles는 test_business_ingest.py와 동일).
@pytest.fixture(autouse=True, scope="module")
def _compile_jsonb():
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.types import JSON

    @compiles(JSON, "sqlite")
    def _compile_json(element, compiler, **kw):
        return "JSON"

    @compiles(JSONB, "sqlite")
    def _compile_jsonb(element, compiler, **kw):
        return "JSON"


@pytest.fixture(scope="function")
def db():
    """In-memory SQLite 테스트 DB — 필요한 테이블만 생성."""
    from app.db.models import BusinessOverviewCache

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BusinessOverviewCache.__table__, BusinessResearchJob.__table__, CorpCodeMap.__table__])

    with Session(engine) as sess:
        yield sess


@pytest.fixture
def _settings():
    """Mock Settings — DART/LLM 키."""
    return MagicMock(dart_api_key="KEY", ollama_api_key="OLLAMA", insight_model="m")


def _seed_corp(db: Session, code: str = "005930"):
    """종목코드 매핑 시드(종목명 해석용)."""

    db.add(CorpCodeMap(stock_code=code, corp_code="CORP", corp_name="X"))
    db.commit()


def _seed_overview(db: Session, code: str, payload: dict | None = None):
    """BusinessOverviewCache 시드."""
    _payload = payload or {"stock_code": code, "sections": [], "source_reports": [], "research_summary": None}
    row = BusinessOverviewCache(
        stock_code=code,
        stock_name="테스트",
        as_of_annual_rcept="2024.12",
        source_reports=_payload.get("source_reports", []),
        inputs_hash="hash1",
        payload=_payload,
        cached_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()


# ── orchestrator 테스트 ───────────────────────────────────────────────────
def test_enqueue_dedup(db: Session, _settings: MagicMock):
    """enqueue: 동일 종목의 pending/running job 있으면 재사용."""
    job1 = business_research.enqueue(db, "005930", "test guideline")
    assert job1.status == "pending"
    assert job1.guideline == "test guideline"

    job2 = business_research.enqueue(db, "005930", "different")
    assert job2.id == job1.id  # 같은 job 반환
    assert job2.guideline == "test guideline"  # 첫 guideline 유지


def test_enqueue_new(db: Session, _settings: MagicMock):
    """enqueue: 기존 job 없으면 신규 생성."""
    job = business_research.enqueue(db, "005930", "guideline")
    assert job.status == "pending"
    assert job.guideline == "guideline"
    assert job.requested_at is not None


def test_claim_next(db: Session, _settings: MagicMock):
    """claim_next: 가장 오래된 pending 반환."""
    business_research.enqueue(db, "005930", "g1")
    business_research.enqueue(db, "000660", "g2")
    job = business_research.claim_next(db)
    assert job is not None
    assert job.stock_code in {"005930", "000660"}
    assert job.status == "pending"


def test_claim_next_reclaims_stale(db: Session, _settings: MagicMock):
    """claim_next: 오래된 running 회수(좀비)."""
    job = business_research.enqueue(db, "005930", "g")
    job.status = "running"
    job.started_at = datetime.now(UTC)  # 좀비 아님(최신)
    db.commit()

    claimed = business_research.claim_next(db)
    assert claimed is None  # running이 좀비가 아니므로 회수 안 함

    # 좀비로 만들기
    from datetime import timedelta

    job.started_at = datetime.now(UTC) - timedelta(minutes=31)
    db.commit()

    claimed = business_research.claim_next(db)
    assert claimed is not None
    assert claimed.status == "pending"  # 회수되어 pending로 재설정


def test_latest_job(db: Session, _settings: MagicMock):
    """latest_job: 최신 job 반환."""
    j1 = business_research.enqueue(db, "005930", "g1")
    business_research.enqueue(db, "000660", "g2")
    assert business_research.latest_job(db, "005930") == j1
    assert business_research.latest_job(db, "999999") is None


def test_run_job_success(db: Session, _settings: MagicMock, monkeypatch):
    """run_job: LLM 정상 응답 → research_summary 병합."""
    _seed_corp(db, "005930")

    # Mock LLM: 스키마 맞춘 JSON 반환
    mock_llm = MagicMock()
    # agent.run_stage는 done=True일 때 result 부분만 반환
    mock_result = {
        "vendors": [{"name": "공급사A", "role": "원재료", "note": "note"}],
        "customers": [{"name": "고객B", "role": "납품", "note": ""}],
        "competitors": [{"name": "경쟁C", "role": "동종", "note": ""}],
        "value_chain": [{"stage": "생산", "direction": "downstream", "entity": "고객B", "note": ""}],
        "narrative_md": "테스트 서술",
    }
    mock_llm.chat.return_value = json.dumps({"done": True, "result": mock_result})

    # Mock review_loop: result_is_error=False → 바꿔치 (procedure_sound=True)
    class _MockReviewLoop:
        result_is_error = staticmethod(lambda r: False)

        @staticmethod
        def run_with_review(llm, model, producer, reviewer_system, **kw):
            # agent.run_stage는 result.get("result")를 반환
            return mock_result  # producer(None)와 동일하게 mock_result 반환

    from app.services.business_research import orchestrator

    monkeypatch.setattr(orchestrator, "get_llm", lambda _: mock_llm)
    monkeypatch.setattr(orchestrator, "review_loop", _MockReviewLoop())
    monkeypatch.setattr(orchestrator.agent, "run_stage", lambda *a, **kw: json.loads(mock_llm.chat()))
    monkeypatch.setattr(business_ingest, "company_service", MagicMock(report_stock_name=lambda db, c, **_: "테스트종목"))

    job = business_research.enqueue(db, "005930", "guideline")
    business_research.run_job(db, job)

    db.refresh(job)
    assert job.status == "done"
    assert job.progress == 100

    # 캐시 병합 확인
    cached = db.scalar(select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == "005930"))
    assert cached is not None
    summary = cached.payload.get("research_summary")
    assert summary is not None
    assert summary["vendors"] == [{"name": "공급사A", "role": "원재료", "note": "note"}]
    assert summary["narrative_md"] == "테스트 서술"
    assert summary["guideline"] == "guideline"


def test_run_job_llm_not_set(db: Session, _settings: MagicMock, monkeypatch):
    """run_job: LLM 미설정 → job.failed."""
    from app.services.business_research import orchestrator

    _settings.ollama_api_key = None  # LLM 없음
    monkeypatch.setattr(orchestrator, "get_llm", lambda _: None)

    job = business_research.enqueue(db, "005930", "g")
    business_research.run_job(db, job)

    db.refresh(job)
    assert job.status == "failed"
    assert "LLM 미설정" in job.error


def test_run_job_llm_error_marker(db: Session, _settings: MagicMock, monkeypatch):
    """run_job: LLM 에러 마커(_error/_note/_partial) → job.failed."""
    from app.services.business_research import orchestrator

    _seed_corp(db, "005930")

    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"_error": "LLM 실패", "_partial": True}'
    mock_error_result = {"_error": "LLM 실패", "_partial": True}

    class _MockReviewLoop:
        result_is_error = staticmethod(lambda r: True)  # 에러 마커로 간주

        @staticmethod
        def run_with_review(llm, model, producer, reviewer_system, **kw):
            return mock_error_result  # 에러 결과 반환

    monkeypatch.setattr(orchestrator, "get_llm", lambda _: mock_llm)
    monkeypatch.setattr(orchestrator, "review_loop", _MockReviewLoop())
    monkeypatch.setattr(orchestrator.agent, "run_stage", lambda *a, **kw: json.loads(mock_llm.chat()))
    monkeypatch.setattr(business_ingest, "company_service", MagicMock(report_stock_name=lambda db, c, **_: "X"))

    job = business_research.enqueue(db, "005930", "g")
    business_research.run_job(db, job)

    db.refresh(job)
    assert job.status == "failed"
    assert "LLM 산출 실패" in job.error


# ── _merge_research_into_cache 테스트 ───────────────────────────────────────
def test_merge_into_cache_creates_stub(db: Session):
    """_merge_research_into_cache: 기존 overview 없으면 스텁 생성."""
    summary = {"guideline": "test", "vendors": [], "customers": [], "competitors": [], "value_chain": [], "narrative_md": "narr", "generated_at": "2025-01-01T00:00:00Z", "model": "m"}
    business_ingest._merge_research_into_cache(db, "005930", summary)

    cached = db.scalar(select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == "005930"))
    assert cached is not None
    assert cached.payload["research_summary"] == summary
    assert cached.payload["sections"] == []  # 스텁: 빈 섹션
    assert cached.inputs_hash == ""


def test_merge_into_cache_preserves_meta(db: Session):
    """_merge_research_into_cache: inputs_hash/source_reports/as_of_annual_rcept 보존."""
    # 기존 캐치(조립된 overview)
    _seed_overview(
        db,
        "005930",
        payload={
            "stock_code": "005930",
            "sections": [{"id": "test"}],
            "source_reports": [{"rcept_no": "r1", "kind": "annual", "period": "2024.12", "is_base": True}],
            "research_summary": None,
        },
    )
    summary = {"guideline": "g", "vendors": [], "customers": [], "competitors": [], "value_chain": [], "narrative_md": "n", "generated_at": "2025-01-01T00:00:00Z", "model": "m"}
    business_ingest._merge_research_into_cache(db, "005930", summary)

    cached = db.scalar(select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == "005930"))
    assert cached.inputs_hash == "hash1"  # 보존
    assert cached.as_of_annual_rcept == "2024.12"  # 보존
    assert cached.source_reports == [{"rcept_no": "r1", "kind": "annual", "period": "2024.12", "is_base": True}]  # 보존
    assert cached.payload["research_summary"] == summary  # 갱신
    assert cached.payload["sections"] == [{"id": "test"}]  # 기존 섹션 유지
