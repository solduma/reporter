"""extract_ontology_refs 비즈니스 온톨로지 ID 방출 확장 단위 테스트.

재무 온톨로지 refs(기존) + 비즈니스 온톨로지 정준 노드 ID(신규)가 함께 방출되는지 검증.
DB 는 SQLite 인메모리 + business_ontology_node/edge create_all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, BusinessOntologyEdge, BusinessOntologyNode
from app.services.deepdive.ontology_refs import extract_ontology_refs


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[BusinessOntologyNode.__table__, BusinessOntologyEdge.__table__]
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_no_db_returns_financial_refs_only():
    """db 없으면 재무 온톨로지 refs 만(하위호환) — 비즈니스 항목 없음."""
    report = {"overview": {"per": 12.3, "pbr": 1.1}}
    refs = extract_ontology_refs(report)
    # 재무 온톨로지 매핑이 있으면 방출, 없어도 비즈니스 항목은 단정히 없어야 한다.
    assert all(r["stage"] != "business" for r in refs)


def test_business_canonical_nodes_emitted(db):
    """정준 노드는 OntologyRef 로 방출 — key·ontology_id = 정준 ID, label = 한글명."""
    db.add(
        BusinessOntologyNode(
            stock_code="005930",
            node_type="product",
            canonical_id="PRD_SEMI_DRAM",
            korean_name="DRAM",
            status="canonical",
            confidence=0.9,
        )
    )
    db.add(
        BusinessOntologyNode(
            stock_code="005930",
            node_type="company",
            canonical_id="CMP_KRX_005930",
            korean_name="삼성전자",
            status="canonical",
        )
    )
    db.commit()
    refs = extract_ontology_refs({}, db=db, stock_code="005930")
    biz = [r for r in refs if r["stage"] == "business"]
    ids = {r["ontology_id"] for r in biz}
    assert {"PRD_SEMI_DRAM", "CMP_KRX_005930"} == ids
    dram = next(r for r in biz if r["ontology_id"] == "PRD_SEMI_DRAM")
    assert dram["label"] == "DRAM" and dram["description"] == "product"


def test_pending_review_nodes_excluded(db):
    """pending_review(미정준) 노드는 정준 ID 가 없어 방출 제외."""
    db.add(
        BusinessOntologyNode(
            stock_code="005930",
            node_type="product",
            canonical_id=None,
            korean_name="謎의제품",
            status="pending_review",
        )
    )
    db.commit()
    refs = extract_ontology_refs({}, db=db, stock_code="005930")
    assert [r for r in refs if r["stage"] == "business"] == []


def test_wrong_stock_code_emits_nothing(db):
    """다른 종목 노드는 방출되지 않음 — stock_code 필터."""
    db.add(
        BusinessOntologyNode(
            stock_code="000660",
            node_type="product",
            canonical_id="PRD_SEMI_DRAM",
            korean_name="DRAM",
            status="canonical",
        )
    )
    db.commit()
    refs = extract_ontology_refs({}, db=db, stock_code="005930")
    assert [r for r in refs if r["stage"] == "business"] == []


def test_financial_and_business_refs_coexist(db):
    """재무 온톨로지 refs 와 비즈니스 온톨로지 refs 가 함께 방출."""
    db.add(
        BusinessOntologyNode(
            stock_code="005930",
            node_type="industry",
            canonical_id="IND_GICS_45102010",
            korean_name="반도체",
            status="canonical",
        )
    )
    db.commit()
    report = {"overview": {"per": 12.3}}  # per → 재무 온톨로지 매핑
    refs = extract_ontology_refs(report, db=db, stock_code="005930")
    stages = {r["stage"] for r in refs}
    assert "business" in stages
