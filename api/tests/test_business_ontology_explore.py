"""explore_node 서비스 단위 테스트 — 노드 중심 cross-stock 1-hop 탐색.

정적 온톨로지(패키지 normalizer)는 실제 동작. DB 는 SQLite 인메모리 + create_all.
persist_ontology(business_ingest) 로 시드 데이터를 영속화한 뒤 explore_node 결과를 검증.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    BusinessOntologyEdge,
    BusinessOntologyNode,
    BusinessOverviewCache,
    CorpCodeMap,
    SegmentSales,
)
from app.domain.business_research import OntologyMention
from app.services import business_ingest as bi
from app.services import business_ontology as bo_svc


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BusinessOntologyNode.__table__,
            BusinessOntologyEdge.__table__,
            BusinessOverviewCache.__table__,
            CorpCodeMap.__table__,
            SegmentSales.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# --- (a) 회사 focal → 제품/원재료/산업 이웃 ---
def test_explore_company_neighbors(db):
    bi.persist_ontology(
        db,
        "005930",
        [
            OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9),
            OntologyMention("raw_material", "실리콘 웨이퍼", "uses_material"),
        ],
        "R1",
        "삼성전자",
        induty_code="C28",
    )
    g = bo_svc.explore_node(db, "CMP_KRX_005930")
    assert g is not None
    assert g["focal"]["node_type"] == "company"
    assert g["focal"]["korean_name"] == "삼성전자"
    types = {n["edge_type"] for n in g["neighbors"]}
    assert {"manufactures", "uses_material", "operates_in"} <= types
    # manufactures 이웃은 direction=out(focal 이 src).
    dram = next(n for n in g["neighbors"] if n["edge_type"] == "manufactures")
    assert dram["id"] == "PRD_SEMI_DRAM" and dram["direction"] == "out"
    assert dram["share"] == 0.42
    # 엣지 응답에는 focal→DRAM 관계 포함.
    assert any(e["edge_type"] == "manufactures" and e["src"] == "CMP_KRX_005930" for e in g["edges"])


# --- (b) 제품 focal → 역방향 manufactures 로 다수 회사(cross-stock) ---
def test_explore_product_cross_stock(db):
    db.add(CorpCodeMap(stock_code="000660", corp_code="00164742", corp_name="SK하이닉스"))
    db.commit()
    bi.persist_ontology(
        db, "005930", [OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9)],
        "R1", "삼성전자",
    )
    bi.persist_ontology(
        db, "000660", [OntologyMention("product", "DRAM", "manufactures", 0.30, "2024.12", "q", 0.9)],
        "R2", "SK하이닉스",
    )
    g = bo_svc.explore_node(db, "PRD_SEMI_DRAM")
    assert g is not None
    assert g["focal"]["node_type"] == "product"
    companies = [n for n in g["neighbors"] if n["edge_type"] == "manufactures"]
    assert {c["id"] for c in companies} == {"CMP_KRX_005930", "CMP_KRX_000660"}
    # 제품 focal 이 dst 이므로 direction=in.
    assert all(c["direction"] == "in" for c in companies)
    # 두 stock_code 인스턴스가 하나의 focal 로 통합.
    assert g["focal"]["korean_name"]


# --- (c) 산업 focal → GICS 형제 sub-industry + operates_in 회사 ---
def test_explore_industry_siblings_and_companies(db):
    bi.persist_ontology(db, "005930", [], "R1", "삼성전자", induty_code="C28")
    # 산업 노드의 canonical_id 추출.
    ind = db.scalar(
        select(BusinessOntologyNode).where(BusinessOntologyNode.node_type == "industry")
    )
    assert ind is not None and ind.canonical_id
    g = bo_svc.explore_node(db, ind.canonical_id)
    assert g is not None
    assert g["focal"]["node_type"] == "industry"
    assert g["focal"]["gics_code"]
    # operates_in 역방향 → 삼성전자 회사 이웃.
    companies = [n for n in g["neighbors"] if n["edge_type"] == "operates_in"]
    assert any(c["id"] == "CMP_KRX_005930" for c in companies)
    # GICS 형제 sub-industry 합성 이웃.
    siblings = [n for n in g["neighbors"] if n["edge_type"] == "sibling_industry"]
    assert len(siblings) >= 1
    assert all(s["node_type"] == "industry" for s in siblings)


# --- (d) 원재료 focal → 역방향 uses_material 회사 ---
def test_explore_material_reverse(db):
    bi.persist_ontology(
        db, "005930", [OntologyMention("raw_material", "실리콘 웨이퍼", "uses_material")], "R1", "삼성전자"
    )
    # 실리콘 웨이퍼 정준 ID 추출.
    mat = db.scalar(
        select(BusinessOntologyNode).where(BusinessOntologyNode.node_type == "raw_material")
    )
    assert mat is not None and mat.canonical_id
    g = bo_svc.explore_node(db, mat.canonical_id)
    assert g is not None
    assert g["focal"]["node_type"] == "raw_material"
    users = [n for n in g["neighbors"] if n["edge_type"] == "uses_material"]
    assert any(u["id"] == "CMP_KRX_005930" for u in users)
    assert all(u["direction"] == "in" for u in users)


# --- (e) 미수집/알 수 없는 노드 → None ---
def test_explore_unknown_returns_none(db):
    assert bo_svc.explore_node(db, "CMP_KRX_999999") is None
    assert bo_svc.explore_node(db, "PRD_NONEXISTENT_XYZ") is None
