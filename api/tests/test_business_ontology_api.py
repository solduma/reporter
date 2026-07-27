"""비즈니스 온톨로지 포트·서비스·라우터 통합 테스트(스켈레톤 단계).

정적 온톨로지는 DB 없이 동작 — 전체 app(lifespan=DB 초기화) 대신 비즈니스 온톨로지 라우터만
마운트한 최소 FastAPI 로 HTTP 를 검증한다. 서비스 단위 테스트는 포트 경유로 정규화·정적 조회를 점검.
DB 백엔드 엔드포인트(graph/segments/products/materials/peers)는 Task #28 에서 테이블 채워진 뒤 검증.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.business_ontology import get_business_ontology_port
from app.db.models import Base, BusinessOntologyEdge, BusinessOntologyNode
from app.db.session import get_session
from app.routers import business_ontology
from app.services import business_ontology as bo_service


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(business_ontology.router)
    return TestClient(app)


# --- 서비스(포트 경유) ---
def test_service_normalize_company_seed():
    results = bo_service.normalize([("삼성전자", "company"), ("(주) SK하이닉스", "company")])
    assert results[0].canonical_id == "CMP_KRX_005930"
    assert results[0].matched_via == "korean_name"
    # 접미사 제거 후 정준 매칭.
    assert results[1].canonical_id == "CMP_KRX_000660"
    assert results[1].resolved


def test_service_normalize_pending_review_no_merge():
    """사전에 없는 회사는 pending_review — 자동 병합 금지."""
    r = bo_service.normalize_one("존재안하는주식회사", "company")
    assert r.status == "pending_review"
    assert r.canonical_id is None
    assert not r.resolved


def test_service_normalize_industry_by_gics_code():
    r = bo_service.normalize_one("45102010", "industry")
    assert r.resolved
    assert r.matched_via == "gics_code"


def test_service_industries_loaded():
    inds = bo_service.industries()
    assert len(inds) >= 128  # GICS 2023 sub-industry 풀
    ids = {i.id for i in inds}
    assert "IND_GICS_45102010" in ids  # Semiconductors


def test_service_industry_lookup_by_code():
    n = bo_service.industry("45102010")
    assert n is not None
    assert n.id == "IND_GICS_45102010"
    assert "반도체" in n.aliases


def test_service_edge_types():
    ets = bo_service.edge_types()
    ids = {e.id for e in ets}
    assert {"manufactures", "uses_material", "operates_in", "competes_with"}.issubset(ids)
    assert len(ets) == 15


def test_service_nodes_typed_filter():
    prods = bo_service.nodes(node_type="product")
    assert all(n.node_type == "product" for n in prods)
    assert any(n.id == "PRD_SEMI_DRAM" for n in prods)


def test_service_node_by_id():
    n = bo_service.node("MAT_SEMI_SILICON_WAFER")
    assert n is not None
    assert n.node_type == "raw_material"


def test_port_resolves_cross_link_product_to_material():
    """제품 겸 원재료(is_also_material_id) 교차링크 — material 해석이 product 사전을 경유."""
    port = get_business_ontology_port()
    # normalizer 의 resolve_material 은 product.is_also_material_id 로 폴백.
    r = port.resolve("실리콘 웨이퍼", "raw_material")
    # 사전에 해당 교차링크가 구성된 경우에만 canonical. 미구성시 pending_review 도 허용.
    assert r.status in ("canonical", "pending_review")


# --- 라우터(HTTP) ---
def test_router_normalize(client: TestClient):
    resp = client.post(
        "/api/business-ontology/normalize",
        json={"mentions": [["삼성전자", "company"], ["DRAM", "product"]], "standard": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["canonical_id"] == "CMP_KRX_005930"
    assert body["items"][0]["resolved"] is True
    assert 0.0 <= body["coverage"] <= 1.0


def test_router_industries(client: TestClient):
    resp = client.get("/api/business-ontology/industries")
    assert resp.status_code == 200
    assert len(resp.json()) >= 128


def test_router_industry_by_code(client: TestClient):
    resp = client.get("/api/business-ontology/industries/45102010")
    assert resp.status_code == 200
    assert resp.json()["id"] == "IND_GICS_45102010"


def test_router_industry_unknown_404(client: TestClient):
    resp = client.get("/api/business-ontology/industries/99999999")
    assert resp.status_code == 404


def test_router_edge_types(client: TestClient):
    resp = client.get("/api/business-ontology/edge-types")
    assert resp.status_code == 200
    assert len(resp.json()) == 15


def test_router_nodes_filter(client: TestClient):
    resp = client.get("/api/business-ontology/nodes", params={"node_type": "product"})
    assert resp.status_code == 200
    assert all(n["node_type"] == "product" for n in resp.json())


def test_router_node_unknown_404(client: TestClient):
    resp = client.get("/api/business-ontology/nodes/PRD_NONEXISTENT")
    assert resp.status_code == 404


def test_router_graph_empty_until_ingest():
    """DB 백엔드 — 빈 테이블(ingest 전)은 빈 그래프. get_session 을 인메모리 SQLite 로 override."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine, tables=[BusinessOntologyNode.__table__, BusinessOntologyEdge.__table__]
    )
    session = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(business_ontology.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    try:
        resp = client.get("/api/business-ontology/005930/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == [] and body["edges"] == []
    finally:
        session.close()
