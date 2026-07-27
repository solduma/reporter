"""비즈니스 온톨로지 LLM 추출 + 노드/엣지 영속화 + 서비스 DB 쿼리 단위 테스트.

LLM(chat) 은 MagicMock. 정규화는 실제 business_ontology 패키지 normalizer 경유(정준 ID 부여).
DB 는 SQLite 인메모리 + create_all(business_ontology_node/edge, corp_code_map, segment_sales).
"""

from __future__ import annotations

from unittest.mock import MagicMock

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


# SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON 으로 렌더.
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


def _ctx(code: str = "005930", name: str = "삼성전자") -> dict:
    return {"stock_code": code, "stock_name": name, "base": {"text": "원문"}, "updates": []}


# --- extract_ontology_entities(LLM NER 파싱) ---
def test_extract_parses_mentions():
    llm = MagicMock()
    llm.chat.return_value = (
        '{"mentions": [{"node_type":"product","name":"DRAM","edge_type":"manufactures",'
        '"share":0.42,"period":"2024.12","source_quote":"DRAM 매출 42%","confidence":0.9}]}'
    )
    ms = bi.extract_ontology_entities(llm, "m", _ctx())
    assert len(ms) == 1
    m = ms[0]
    assert m.node_type == "product" and m.name == "DRAM"
    assert m.edge_type == "manufactures"
    assert m.share == 0.42 and m.source_quote == "DRAM 매출 42%"
    assert m.confidence == 0.9


def test_extract_skips_invalid_node_type_and_empty_name():
    llm = MagicMock()
    llm.chat.return_value = (
        '{"mentions": [{"node_type":"facility","name":"X","edge_type":""},'
        '{"node_type":"product","name":"","edge_type":"manufactures"}]}'
    )
    assert bi.extract_ontology_entities(llm, "m", _ctx()) == []


def test_extract_handles_empty_and_bad_json():
    llm = MagicMock()
    llm.chat.return_value = "not json"
    assert bi.extract_ontology_entities(llm, "m", _ctx()) == []
    llm.chat.return_value = '{"mentions": []}'
    assert bi.extract_ontology_entities(llm, "m", _ctx()) == []


def test_extract_llm_error_returns_empty():
    from app.ports.llm import LLMError

    llm = MagicMock()
    llm.chat.side_effect = LLMError("boom")
    assert bi.extract_ontology_entities(llm, "m", _ctx()) == []


# --- persist_ontology(정규화 + 영속 + 스냅샷) ---
def test_persist_canonical_product_and_pending(db):
    mentions = [
        OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "DRAM 매출 42%", 0.9),
        OntologyMention("product", "존재안하는제품", "manufactures"),
    ]
    snap = bi.persist_ontology(db, "005930", mentions, "R1", "삼성전자")
    nodes = db.scalars(select(BusinessOntologyNode)).all()
    # 회사(주체) + 제품 2 = 3 노드.
    assert len(nodes) == 3
    dram = next(n for n in nodes if n.canonical_id == "PRD_SEMI_DRAM")
    assert dram.status == "canonical" and dram.node_type == "product"
    pend = next(n for n in nodes if n.korean_name == "존재안하는제품")
    assert pend.status == "pending_review" and pend.canonical_id is None
    edges = db.scalars(select(BusinessOntologyEdge)).all()
    assert len(edges) == 2
    assert all(e.edge_type == "manufactures" for e in edges)
    # source_quote 보존(감사증적).
    dram_edge = next(e for e in edges if e.dst_node_id == dram.id)
    assert dram_edge.source_quote == "DRAM 매출 42%"
    # 스냅샷은 DB 행에서 재구성.
    assert any(n["id"] == "PRD_SEMI_DRAM" for n in snap["nodes"])
    assert any(e["edge_type"] == "manufactures" for e in snap["edges"])


def test_persist_company_via_corpmap_db(db):
    """시드 사전이 아닌 회사 → CorpCodeMap DB exact 로 정준화(CMP_KRX_<stock_code>)."""
    db.add(CorpCodeMap(stock_code="999999", corp_code="00000000", corp_name="테스트회사"))
    db.commit()
    mentions = [OntologyMention("company", "테스트회사", "competes_with")]
    bi.persist_ontology(db, "005930", mentions, "R1", "삼성전자")
    node = db.scalar(
        select(BusinessOntologyNode).where(BusinessOntologyNode.korean_name == "테스트회사")
    )
    assert node is not None
    assert node.canonical_id == "CMP_KRX_999999"
    assert node.status == "canonical"


def test_persist_induty_code_creates_operates_in(db):
    """induty_code(C28) → GICS 산업 노드 + operates_in 엣지."""
    bi.persist_ontology(db, "005930", [], "R1", "삼성전자", induty_code="C28")
    ind = db.scalar(
        select(BusinessOntologyNode).where(BusinessOntologyNode.node_type == "industry")
    )
    assert ind is not None and ind.canonical_id  # GICS 정준 매핑
    edge = db.scalar(
        select(BusinessOntologyEdge).where(BusinessOntologyEdge.edge_type == "operates_in")
    )
    assert edge is not None and edge.dst_node_id == ind.id


def test_persist_idempotent_on_replay(db):
    mentions = [OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9)]
    bi.persist_ontology(db, "005930", mentions, "R1", "삼성전자")
    bi.persist_ontology(db, "005930", mentions, "R1", "삼성전자")
    nodes = db.scalars(select(BusinessOntologyNode)).all()
    edges = db.scalars(select(BusinessOntologyEdge)).all()
    # 동일 mention 재처리 → 노드/엣지 중복 증식 없음.
    assert len(nodes) == 2  # 회사 + DRAM
    assert len(edges) == 1


# --- 서비스 DB 쿼리 ---
def test_company_graph_empty(db):
    assert bo_svc.company_graph(db, "005930") == {"nodes": [], "edges": []}


def test_company_segments(db):
    db.add(
        SegmentSales(
            stock_code="005930",
            bsns_year="2024",
            report_code="11011",
            segment_type="제품",
            segment_name="DRAM",
            revenue=63e9,
            ratio_pct=42.0,
        )
    )
    db.commit()
    segs = bo_svc.company_segments(db, "005930")
    assert len(segs) == 1 and segs[0]["segment_name"] == "DRAM"
    assert bo_svc.company_segments(db, "005930", year="2023") == []


def test_company_products_and_materials(db):
    bi.persist_ontology(
        db,
        "005930",
        [
            OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9),
            OntologyMention("raw_material", "실리콘 웨이퍼", "uses_material"),
        ],
        "R1",
        "삼성전자",
    )
    prods = bo_svc.company_products(db, "005930")
    assert len(prods) == 1 and prods[0]["edge_type"] == "manufactures"
    mats = bo_svc.company_materials(db, "005930")
    assert len(mats) == 1 and mats[0]["edge_type"] == "uses_material"


def test_industry_companies_peers(db):
    """operates_in 산업 노드 역방향 → 동종업 종목."""
    bi.persist_ontology(db, "005930", [], "R1", "삼성전자", induty_code="C28")
    peers = bo_svc.industry_companies(db, "45102010")  # Semiconductors GICS 코드
    assert any(p["stock_code"] == "005930" for p in peers)


# --- Phase 3b: 리서치 → 온톨로지 승격 ---
def test_promote_research_maps_roles_to_edges(db):
    """vendors→supplies, customers→supplies_to, competitors→competes_with, value_chain→part_of_value_chain."""
    db.add(CorpCodeMap(stock_code="000660", corp_code="00164742", corp_name="SK하이닉스"))
    db.commit()
    summary = {
        "vendors": [{"name": "SK하이닉스", "role": "부품 공급", "note": "n1"}],
        "customers": [{"name": "SK하이닉스", "role": "납품처", "note": "n2"}],
        "competitors": [{"name": "SK하이닉스", "role": "경쟁", "note": "n3"}],
        "value_chain": [
            {"stage": "원료 조달", "direction": "upstream", "entity": "SK하이닉스", "note": "n4"}
        ],
    }
    snap = bi.promote_research_to_ontology(db, "005930", summary)
    edges = db.scalars(select(BusinessOntologyEdge)).all()
    types = {e.edge_type for e in edges}
    assert {"supplies", "supplies_to", "competes_with", "part_of_value_chain"} == types
    # chain_stage 매핑: 원료 조달 → inbound.
    vc = next(e for e in edges if e.edge_type == "part_of_value_chain")
    assert vc.chain_stage == "inbound"
    # 회사 노드 1(주체) + SK하이닉스 1(대상) — 역할이 달라도 동일 대상 노드 재사용.
    nodes = db.scalars(select(BusinessOntologyNode)).all()
    assert len(nodes) == 2
    assert any(n["id"] for n in snap["nodes"])


def test_promote_vendor_material_uses_uses_material(db):
    """공급자가 원재료로 정준화되면 uses_material 엣지(LLM 추출과 일관성)."""
    summary = {"vendors": [{"name": "실리콘 웨이퍼", "role": "원재료 공급", "note": ""}]}
    bi.promote_research_to_ontology(db, "005930", summary)
    edges = db.scalars(select(BusinessOntologyEdge)).all()
    # 실리콘 웨이퍼가 material 사전에 있으면 uses_material, 아니면 supplies(company pending).
    assert edges[0].edge_type in ("uses_material", "supplies")


def test_promote_chain_stage_keywords(db):
    assert bi._map_chain_stage("생산 공정") == "operations"
    assert bi._map_chain_stage("유통망") == "outbound"
    assert bi._map_chain_stage("서비스") == "service"
    assert bi._map_chain_stage("") is None
    assert bi._map_chain_stage("알 수 없는 단계") is None


def test_merge_research_into_cache_sets_ontology_snapshot(db):
    """_merge_research_into_cache 가 리서치 승격 후 payload["ontology"] 를 갱신."""
    db.add(CorpCodeMap(stock_code="000660", corp_code="00164742", corp_name="SK하이닉스"))
    db.commit()
    summary = {
        "vendors": [{"name": "SK하이닉스", "role": "", "note": ""}],
        "customers": [],
        "competitors": [],
        "value_chain": [],
        "narrative_md": "",
        "guideline": "",
        "model": "m",
    }
    bi._merge_research_into_cache(db, "005930", summary)
    row = db.scalar(
        select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == "005930")
    )
    assert row is not None
    ont = row.payload.get("ontology")
    assert ont and any(e["edge_type"] == "supplies" for e in ont["edges"])
