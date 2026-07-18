"""주담(IR) 인터뷰 — 민감변수 추출·80개 캡·job 큐 (LLM 호출은 목킹)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DeepDiveReport, IrInterviewJob, IrInterviewReport
from app.services import ir_interview


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[DeepDiveReport.__table__, IrInterviewJob.__table__, IrInterviewReport.__table__],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_sensitive_context_extracts_valuation_assumptions(db):
    # 민감변수 컨텍스트 = methods(가정·신뢰도·경고) + thesis/redflags + entry/target.
    rep = DeepDiveReport(
        stock_code="000001",
        valuation_json={
            "stock_type": "growth", "entry_case": "성장주",
            "final_target_price": 100000, "final_upside_pct": 30.0,
            "methods": [
                {"label": "PER", "confidence": "하", "assumptions": {"target_per": 28},
                 "note": "forward EPS 불확실", "applicable": True},
            ],
        },
        thesis_json={"drivers": ["신규 수주"]},
        redflags_json={"severity": "양호"},
    )
    ctx = ir_interview._sensitive_context(rep)
    assert ctx["stock_type"] == "growth"
    assert ctx["final_target_price"] == 100000
    assert ctx["methods"][0]["assumptions"] == {"target_per": 28}
    assert ctx["methods"][0]["confidence"] == "하"
    assert ctx["thesis"] == {"drivers": ["신규 수주"]}


def test_generate_requires_valuation(db):
    # 딥다이브 밸류 결과 없으면 생성 거부.
    with pytest.raises(RuntimeError, match="딥다이브"):
        ir_interview.generate(db, "000001")


def test_enqueue_dedupes_active(db):
    # 진행 중 job 이 있으면 새로 만들지 않고 그것을 반환.
    j1 = ir_interview.enqueue(db, "000001")
    j2 = ir_interview.enqueue(db, "000001")
    assert j1.id == j2.id
    assert db.query(IrInterviewJob).count() == 1


def test_claim_next_marks_running(db):
    job = ir_interview.enqueue(db, "000001")
    claimed = ir_interview.claim_next(db)
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert ir_interview.claim_next(db) is None  # 더 이상 pending 없음


def test_total_cap_at_80(db, monkeypatch):
    # 아이템 10개 × 질문 10개 = 100개여도 80개로 캡.
    rep = DeepDiveReport(stock_code="000001", valuation_json={"methods": []})
    db.add(rep)
    db.commit()

    monkeypatch.setattr(ir_interview, "get_llm", lambda s: object())
    monkeypatch.setattr(
        ir_interview, "_derive_items",
        lambda llm, model, ctx, context: [{"item": f"item{i}", "why_matters": "w",
                                           "linked_valuation_assumption": "a"} for i in range(10)],
    )
    monkeypatch.setattr(
        ir_interview, "_questions_for_item",
        lambda llm, model, ctx, item, context: [
            {"q": f"q{i}", "intent": "i", "valuation_link": "l", "expected_signal": "s"}
            for i in range(10)
        ],
    )
    # tools.resolve_corp_code·ToolContext 는 실제 호출되지만 DB만 접근 → 그대로 둠.
    monkeypatch.setattr(ir_interview.tools, "resolve_corp_code", lambda db, code: None)
    result = ir_interview.generate(db, "000001")
    assert result["total_questions"] == 80  # 100 → 80 캡
