"""재무 온톨로지 포트·서비스·라우터 통합 테스트(2차-B).

온톨로지는 정적 데이터라 DB 없이 동작 — 전체 app(lifespan=DB 초기화) 대신 온톨로지 라우터만
마운트한 최소 FastAPI 로 HTTP 를 검증한다. 서비스 단위 테스트는 포트 경유로 정규화·비율을 점검.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.financial_ontology import get_ontology_port
from app.routers import ontology
from app.services import ontology as ontology_service


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ontology.router)
    return TestClient(app)


# --- 서비스(포트 경유) ---
def test_service_normalize_korean():
    results = ontology_service.normalize(["매출채권", "영업이익", "없는항목"])
    assert [r.id for r in results] == ["BS_CA_AR", "IS_OP_INCOME", None]
    assert results[0].matched_via == "korean_name"
    assert results[2].id is None


def test_service_normalize_dart_taxonomy():
    results = ontology_service.normalize(["ifrs-full_CashAndCashEquivalents"], standard="dart")
    assert results[0].id == "BS_CA_CASH"
    assert results[0].matched_via == "taxonomy"


def test_financial_column_ontology_mapping_is_valid():
    """A3 매핑 메타의 모든 account ID 는 실제 온톨로지 계정, ratio ID 는 실제 비율이어야 한다."""
    port = get_ontology_port()
    account_ids = {oid for oid, kind in ontology_service.FINANCIAL_COLUMN_ONTOLOGY.values() if kind == "account"}
    for aid in account_ids:
        assert port.account(aid) is not None, f"매핑 account ID 가 온톨로지에 없음: {aid}"
    ratio_ids = {rid for rid, kind in ontology_service.FINANCIAL_COLUMN_ONTOLOGY.values() if kind == "ratio"}
    all_ratio_ids = {r.id for r in port.list_ratios()}
    for rid in ratio_ids:
        assert rid in all_ratio_ids, f"매핑 ratio ID 가 온톨로지에 없음: {rid}"


def test_financial_row_to_ontology_values_and_stored_ratios():
    """계정 종류 컬럼은 RatioEngine 입력 dict 로, 비율 종류 컬럼은 stored-ratio dict 로."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        revenue=1000.0, operating_income=200.0, net_income=150.0, depreciation=30.0, capex=80.0,
        eps=1.5, bps=50000.0, per=10.0, pbr=0.8, roe=12.0, psr=1.2, ev_ebitda=5.0, div_yield=2.0,
    )
    values = ontology_service.financial_row_to_ontology_values(row)
    assert values == {
        "IS_REV_TOTAL": 1000.0, "IS_OP_INCOME": 200.0, "IS_NI_PARENT": 150.0,
        "CF_OP_DEPR": 30.0, "CF_INV_PPE": 80.0,
    }
    stored = ontology_service.financial_row_stored_ratios(row)
    assert stored == {
        "eps": 1.5, "bvps": 50000.0, "per": 10.0, "pbr": 0.8,
        "roe": 12.0, "psr": 1.2, "evebitda": 5.0, "dividend_yield": 2.0,
    }
    # None 컬럼은 스킵
    partial = SimpleNamespace(revenue=None, operating_income=50.0, net_income=None,
                              depreciation=None, capex=None, eps=None, bps=None, per=None,
                              pbr=None, roe=None, psr=None, ev_ebitda=None, div_yield=None)
    assert ontology_service.financial_row_to_ontology_values(partial) == {"IS_OP_INCOME": 50.0}


def test_service_enrich_with_ontology_id():
    """enrich 가 항목 dict 에 ontology_id 를 주입(영속화용). 순서·미매칭 보존."""
    statements = {
        "BS": [
            {"account_id": "ifrs-full_CashAndCashEquivalents", "name": "현금및현금성자산", "amount": 1.0},
            {"account_id": "", "name": "미매칭계정", "amount": 2.0},
        ],
        "IS": [{"account_id": "dart_OperatingIncomeLoss", "name": "영업이익", "amount": 3.0}],
    }
    ontology_service.enrich_with_ontology_id(statements)
    assert statements["BS"][0]["ontology_id"] == "BS_CA_CASH"
    assert statements["BS"][1]["ontology_id"] is None  # 미매칭
    assert statements["IS"][0]["ontology_id"] == "IS_OP_INCOME"
    # 빈 입력은 no-op
    empty: dict[str, list[dict]] = {}
    assert ontology_service.enrich_with_ontology_id(empty) is empty


def test_service_required_accounts():
    req = ontology_service.required_accounts("ebitda_margin")
    assert set(req) == {"IS_OP_INCOME", "IS_OPEX_DEPR", "IS_REV_TOTAL"}


def test_service_calculate_current_ratio():
    r = ontology_service.calculate_one("current_ratio", {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 60})
    assert r.ok
    assert r.value == Decimal(100) / Decimal(60)


def test_service_calculate_missing():
    r = ontology_service.calculate_one("current_ratio", {"BS_CA_TOTAL": 100})
    assert not r.ok
    assert "BS_CL_TOTAL" in r.missing
    assert "missing" in r.reason


def test_service_list_accounts_statement_filter():
    accs = ontology_service.accounts(statement="balance_sheet")
    assert accs
    assert all("balance_sheet" in a.statement for a in accs)


# --- 라우터(HTTP) ---
def test_http_normalize(client: TestClient):
    res = client.post(
        "/api/ontology/normalize",
        json={"terms": ["매출채권", "현금및현금성자산", "없는항목"]},
    )
    assert res.status_code == 200
    body = res.json()
    ids = [i["id"] for i in body["items"]]
    assert ids == ["BS_CA_AR", "BS_CA_CASH", None]
    assert body["coverage"] == pytest.approx(2 / 3)


def test_http_normalize_dart(client: TestClient):
    res = client.post(
        "/api/ontology/normalize",
        json={"terms": ["ifrs-full_CashAndCashEquivalents"], "standard": "dart"},
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["id"] == "BS_CA_CASH"


def test_http_calculate_ratio(client: TestClient):
    res = client.post(
        "/api/ontology/ratio",
        json={"ratio_id": "current_ratio", "values": {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 60}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert float(body["value"]) == pytest.approx(100 / 60)


def test_http_calculate_ratio_missing(client: TestClient):
    res = client.post(
        "/api/ontology/ratio",
        json={"ratio_id": "current_ratio", "values": {"BS_CA_TOTAL": 100}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "BS_CL_TOTAL" in body["missing"]


def test_http_calculate_ratios_multi(client: TestClient):
    res = client.post(
        "/api/ontology/ratios",
        json={
            "ratio_ids": ["current_ratio", "debt_ratio"],
            "values": {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 60, "BS_L_TOTAL": 60, "BS_EQ_TOTAL": 40},
        },
    )
    assert res.status_code == 200
    results = {r["ratio_id"]: r for r in res.json()}
    assert results["current_ratio"]["ok"] is True
    assert results["debt_ratio"]["ok"] is True
    assert float(results["debt_ratio"]["value"]) == pytest.approx(60 / 40)


def test_http_calculate_ebitda(client: TestClient):
    res = client.post(
        "/api/ontology/ratio",
        json={
            "ratio_id": "ebitda_margin",
            "values": {"IS_OP_INCOME": 60, "IS_OPEX_DEPR": 20, "IS_REV_TOTAL": 200},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert float(body["value"]) == pytest.approx(0.4)


def test_http_list_ratios(client: TestClient):
    res = client.get("/api/ontology/ratios")
    assert res.status_code == 200
    assert len(res.json()) == 57
    res = client.get("/api/ontology/ratios", params={"category": "liquidity"})
    assert res.status_code == 200
    assert all(r["category"] == "liquidity" for r in res.json())


def test_http_list_accounts(client: TestClient):
    res = client.get("/api/ontology/accounts", params={"statement": "balance_sheet"})
    assert res.status_code == 200
    assert all("balance_sheet" in a["statement"] for a in res.json())


def test_http_get_account(client: TestClient):
    res = client.get("/api/ontology/accounts/BS_CA_AR")
    assert res.status_code == 200
    assert res.json()["korean_name"] == "매출채권"


def test_http_get_account_404(client: TestClient):
    res = client.get("/api/ontology/accounts/NO_SUCH_ACCOUNT")
    assert res.status_code == 404


def test_http_metric_info(client: TestClient):
    res = client.post(
        "/api/ontology/metric-info",
        json={"keys": ["revenue", "operating_income", "per", "no_such_column"]},
    )
    assert res.status_code == 200
    body = res.json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["revenue"]["ontology_id"] == "IS_REV_TOTAL"
    assert by_key["revenue"]["description"] is not None
    assert by_key["operating_income"]["ontology_id"] == "IS_OP_INCOME"
    assert by_key["per"]["ontology_id"] == "per"
    assert by_key["per"]["description"] is not None
    assert by_key["no_such_column"]["ontology_id"] is None
    assert body["coverage"] == pytest.approx(3 / 4)
