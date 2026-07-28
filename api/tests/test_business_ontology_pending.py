"""pending_review 승격 워크플로 서비스 테스트 — HITL 검수.

정규화 실패 노드(canonical_id=NULL, status=pending_review)를 사람이 canonical 로 승격.
SQLite 인메모리 + create_all. pending 행은 직접 insert 해서 재현(정규화 무매치 상태).
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


def _add_pending(db, stock_code, node_type, name):
    n = BusinessOntologyNode(
        stock_code=stock_code,
        node_type=node_type,
        korean_name=name,
        status="pending_review",
        canonical_id=None,
        confidence=0.0,
    )
    db.add(n)
    db.commit()
    return n


# --- (a) 목록 + fuzzy 후보 ---
def test_list_pending_with_candidates(db):
    db.add(CorpCodeMap(stock_code="005930", corp_code="00126380", corp_name="삼성전자"))
    db.commit()
    bi.persist_ontology(db, "005930", [OntologyMention("product", "DRAM", "manufactures")], "R1", "삼성전자")
    pending = _add_pending(db, "005930", "company", "삼성전자주식회사")  # 회사 pending

    r = bo_svc.list_pending(db)
    assert r["total"] == 1
    item = r["pending"][0]
    assert item["id"] == pending.id
    # CorpCodeMap fuzzy 후보에 삼성전자(CMP_KRX_005930) 가 점수와 함께 나타남.
    cids = [c["canonical_id"] for c in item["candidates"]]
    assert "CMP_KRX_005930" in cids
    cand = next(c for c in item["candidates"] if c["canonical_id"] == "CMP_KRX_005930")
    assert cand["score"] > 0


# --- (b) merge 승격 → explore 가 이웃 노출(엣지는 node PK 기준) ---
def test_promote_merge_visible_in_explore(db):
    # 005930 이 DRAM 생산(canonical PRD_SEMI_DRAM + 회사 노드 + 엣지).
    bi.persist_ontology(
        db, "005930", [OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9)],
        "R1", "삼성전자",
    )
    # pending 제품 "DRAM모듈" + 회사→pending 엣지(정규화 실패해 그대로 보존된 관계).
    pprod = _add_pending(db, "005930", "product", "DRAM모듈")
    company = db.scalar(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.node_type == "company", BusinessOntologyNode.stock_code == "005930"
        )
    )
    db.add(
        BusinessOntologyEdge(
            stock_code="005930", src_node_id=company.id, dst_node_id=pprod.id,
            edge_type="manufactures", period="", confidence=0.9,
        )
    )
    db.commit()

    # 승격 전: PRD_SEMI_DRAM 탐색에 canonical DRAM 의 manufactures 이웃(회사) 1건만.
    # pending "DRAM모듈" 엣지는 pprod 가 정준 집합에 없어 보이지 않음.
    g0 = bo_svc.explore_node(db, "PRD_SEMI_DRAM")
    assert g0 is not None
    mfr0 = [n for n in g0["neighbors"] if n["edge_type"] == "manufactures"]
    assert len(mfr0) == 1

    # merge 승격 — DRAM모듈 행을 기존 PRD_SEMI_DRAM 정준에 합류.
    r = bo_svc.promote_pending(db, pprod.id, "PRD_SEMI_DRAM", "merge")
    assert r["status"] == "canonical"
    assert r["canonical_id"] == "PRD_SEMI_DRAM"

    # 승격 후: 같은 정준 PK 집합에 pprod.id 가 포함돼 회사→pprod 엣지가 이웃으로 추가 노출.
    g1 = bo_svc.explore_node(db, "PRD_SEMI_DRAM")
    assert g1 is not None
    mfr1 = [n for n in g1["neighbors"] if n["edge_type"] == "manufactures"]
    assert len(mfr1) == 2
    assert {c["id"] for c in mfr1} == {"CMP_KRX_005930"}


# --- (c) new 승격 — 신규 정준 발급 ---
def test_promote_new_canonical(db):
    p = _add_pending(db, "005930", "product", "HBM3E")
    r = bo_svc.promote_pending(db, p.id, "PRD_SEMI_HBM3E", "new")
    assert r["status"] == "canonical"
    assert r["canonical_id"] == "PRD_SEMI_HBM3E"
    # explore 가 새 정준을 focal 로 인식(인스턴스 행 존재).
    g = bo_svc.explore_node(db, "PRD_SEMI_HBM3E")
    assert g is not None
    assert g["focal"]["node_type"] == "product"


# --- (d) 잘못된 접두어 / new 중복 → ValueError ---
def test_promote_invalid_prefix(db):
    p = _add_pending(db, "005930", "product", "HBM")
    with pytest.raises(ValueError):
        bo_svc.promote_pending(db, p.id, "MAT_SEMI_HBM", "new")  # product 에 MAT_ 접두어


def test_promote_new_conflict_with_existing(db):
    bi.persist_ontology(db, "005930", [OntologyMention("product", "DRAM", "manufactures")], "R1", "삼성전자")
    p = _add_pending(db, "005930", "product", "DRAM호환")
    with pytest.raises(ValueError):
        bo_svc.promote_pending(db, p.id, "PRD_SEMI_DRAM", "new")  # 이미 존재 → merge 써야 함


# --- (e) reject → 숨김, 목록에서 제외 ---
def test_reject_excludes_from_list(db):
    p = _add_pending(db, "005930", "product", "폐기대상")
    r = bo_svc.reject_pending(db, p.id)
    assert r["status"] == "rejected"
    # pending 목록에서 사라짐.
    lst = bo_svc.list_pending(db)
    assert lst["total"] == 0
    assert all(item["id"] != p.id for item in lst["pending"])


# --- (f) reprocess — 개선된 normalizer로 pending 일괄 재해석 ---
def test_reprocess_promotes_auto_new_product(db):
    """정적 사전에 없는 제품명 → 자동 new 발급(PRD_AUTO_<slug>)으로 승격."""
    p = _add_pending(db, "005930", "product", "Galaxy S24")
    r = bo_svc.reprocess_pending(db)
    assert r["promoted"] == 1
    assert r["still_pending"] == 0
    assert r["total"] == 1
    db.refresh(p)
    assert p.status == "canonical"
    assert p.canonical_id.startswith("PRD_AUTO_")
    # pending 목록에서 사라짐.
    assert bo_svc.list_pending(db)["total"] == 0


def test_reprocess_rejects_company_blacklist(db):
    """회사 NER 오분류(병ㆍ의원) → rejected."""
    p = _add_pending(db, "005930", "company", "병ㆍ의원")
    r = bo_svc.reprocess_pending(db)
    assert r["rejected"] == 1
    db.refresh(p)
    assert p.status == "rejected"
    # reject 는 pending 목록에서 제외.
    assert bo_svc.list_pending(db)["total"] == 0


def test_reprocess_industry_keyword_mapping(db):
    """industry 자유표현(반도체 시장) → GICS 키워드 매핑으로 canonical."""
    p = _add_pending(db, "005930", "industry", "메모리 반도체 시장")
    r = bo_svc.reprocess_pending(db)
    assert r["promoted"] == 1
    db.refresh(p)
    assert p.status == "canonical"
    assert p.canonical_id.startswith("IND_GICS_")


def test_reprocess_node_type_filter(db):
    """node_type 필터 — company만 재처리 시 product pending 유지."""
    _add_pending(db, "005930", "company", "병ㆍ의원")
    pp = _add_pending(db, "005930", "product", "Galaxy S24")
    r = bo_svc.reprocess_pending(db, node_type="company")
    assert r["total"] == 1
    assert r["rejected"] == 1
    # product pending 은 그대로.
    assert bo_svc.list_pending(db)["total"] == 1
    db.refresh(pp)
    assert pp.status == "pending_review"


def test_reprocess_idempotent(db):
    """이미 승격된 노드는 재처리 대상 아님 — 두 번째 실행 시 total=0."""
    _add_pending(db, "005930", "product", "Galaxy S24")
    r1 = bo_svc.reprocess_pending(db)
    assert r1["promoted"] == 1
    r2 = bo_svc.reprocess_pending(db)
    assert r2["total"] == 0
    assert r2["promoted"] == 0
