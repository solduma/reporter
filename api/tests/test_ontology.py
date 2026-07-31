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


def test_compute_account_aggregate_total():
    """port.compute_account 가 account formula 로 총계(BS_L_TOTAL)를 자식 합산해 계산."""
    port = ontology_service._port()
    r = port.compute_account("BS_L_TOTAL", {"BS_CL_TOTAL": 120, "BS_NCL_TOTAL": 26})
    assert r.ok, r.reason
    assert r.value == Decimal("146")


def test_compute_aggregate_accounts_fills_totals():
    """_compute_aggregate_accounts 가 미결측 총계를 채우고 직접 저장값은 덮어쓰지 않는다."""
    port = ontology_service._port()
    values = {
        "BS_CA_TOTAL": 306220075000000.0, "BS_NCA_TOTAL": 327119529000000.0,  # 자산
        "BS_CL_TOTAL": 120603778000000.0, "BS_NCL_TOTAL": 26099850000000.0,  # 부채
        "BS_EQ_PARENT": 473964801000000.0, "BS_EQ_NCI": 671944000000.0,  # 자본
        # BS_A_TOTAL/BS_L_TOTAL/BS_EQ_TOTAL 은 DART 가 저장하지 않는 총계 — 계산 대상
    }
    ontology_service._compute_aggregate_accounts(port, values)
    assert values["BS_A_TOTAL"] == 633339604000000.0
    assert values["BS_L_TOTAL"] == 146703628000000.0
    assert values["BS_EQ_TOTAL"] == 474636745000000.0


def test_is_flow_stock_ratio_classification():
    """flow/stock(IS/CF × BS) 비율만 연환산 대상 — BS/BS·IS/IS 비율은 아니다."""
    port = ontology_service._port()
    assert not ontology_service._is_flow_stock_ratio(port, "debt_ratio")  # BS/BS
    assert not ontology_service._is_flow_stock_ratio(port, "current_ratio")  # BS/BS
    assert not ontology_service._is_flow_stock_ratio(port, "operating_margin")  # IS/IS
    assert ontology_service._is_flow_stock_ratio(port, "roe")  # IS/BS
    assert ontology_service._is_flow_stock_ratio(port, "asset_turnover")  # IS/BS


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


# --- C2: TTM 연환산 + 단위 정규화 + KR 정합 ---
def test_period_to_yq():
    assert ontology_service._period_to_yq("2026.03") == (2026, 1)
    assert ontology_service._period_to_yq("2025.06") == (2025, 2)
    assert ontology_service._period_to_yq("2025.09") == (2025, 3)
    assert ontology_service._period_to_yq("2025.12") == (2025, 4)


def test_ttm_value_four_quarters():
    # TTM at 2024Q4 = 70+80+90+(300-240=60) = 300
    raw = {(2024, 1): 70.0, (2024, 2): 80.0, (2024, 3): 90.0, (2024, 4): 300.0}
    assert ontology_service._ttm_value(raw, (2024, 4)) == 300.0


def test_ttm_value_insufficient_falls_back_to_annual():
    # 4분기 불충족 → None. annual fallback 은 quarterly 데이터 없이 annual 만 있는 신규 상장 등에
    # 의미가 있고, 그 경우 annual 을 그대로 TTM 으로 쓰는 것이 올바름.
    raw = {(2024, 4): 300.0}
    assert ontology_service._ttm_value(raw, (2024, 4)) == 300.0  # annual 원시값 → TTM


def test_ttm_value_no_data_returns_none():
    raw = {(2025, 1): 100.0}  # 분기 1개, 연간 없음 → TTM 불가
    assert ontology_service._ttm_value(raw, (2025, 1)) is None


def test_company_ratios_percentage_scaling(monkeypatch):
    """percentage-unit 계산값은 분수→퍼센트 환산, ratio-unit 은 그대로(저장값과 단위 일치)."""
    from app.services import company_service

    values = {
        "IS_NI_PARENT": 10.0, "BS_EQ_PARENT:closing": 100.0, "BS_EQ_PARENT:opening": 100.0,
        "IS_REV_TOTAL": 1000.0, "BS_A_TOTAL:closing": 500.0, "BS_A_TOTAL:opening": 500.0,
    }
    monkeypatch.setattr(
        ontology_service, "build_ratio_values", lambda db, code, fs_div="CFS": (values, {})
    )
    monkeypatch.setattr(company_service, "theme_names", lambda db, code: [])
    results = {r.ratio_id: r for r in ontology_service.company_ratios(None, "005930")}
    # roe = 10/((100+100)/2) = 0.1 → ×100 = 10.0 (percentage)
    assert results["roe"].value == Decimal("10.0")
    # asset_turnover = 1000/((500+500)/2) = 2.0 (ratio, 환산 X)
    assert results["asset_turnover"].value == Decimal("2.0")


def test_company_ratios_stored_preferred_over_computed(monkeypatch):
    """저장 비율(roe)은 계산 가능해도 저장값 우선 — 단위(퍼센트) 불변."""
    from app.services import company_service

    values = {"IS_NI_PARENT": 10.0, "BS_EQ_PARENT:closing": 100.0, "BS_EQ_PARENT:opening": 100.0}
    monkeypatch.setattr(
        ontology_service, "build_ratio_values",
        lambda db, code, fs_div="CFS": (values, {"roe": 19.16}),
    )
    monkeypatch.setattr(company_service, "theme_names", lambda db, code: [])
    results = {r.ratio_id: r for r in ontology_service.company_ratios(None, "005930")}
    assert float(results["roe"].value) == pytest.approx(19.16)
    assert results["roe"].reason == "stored"


def test_kr_financial_ratio_validation_roe_diff(monkeypatch):
    """roe 계산(연환산) vs stored 를 퍼센트 단위로 비교 — diff 5.26% → not ok."""
    values = {"IS_NI_PARENT": 100.0, "BS_EQ_PARENT:closing": 600.0, "BS_EQ_PARENT:opening": 400.0}
    monkeypatch.setattr(
        ontology_service, "build_ratio_values",
        lambda db, code, fs_div="CFS": (values, {"roe": 19.0, "per": 10.0}),
    )
    items = ontology_service.kr_financial_ratio_validation(None, "005930")
    by = {i["ratio_id"]: i for i in items}
    # roe = 100/((400+600)/2) = 0.2 → ×100 = 20.0 vs stored 19.0 → diff (20-19)/19 ≈ 5.26%
    assert by["roe"]["calculated"] == pytest.approx(20.0)
    assert by["roe"]["ok"] is False
    assert by["roe"]["diff_pct"] == pytest.approx(5.26, abs=0.01)
    # per 는 시장데이터(market_cap) 필요 → no_market_data
    assert by["per"]["reason"] == "no_market_data"
    assert by["per"]["ok"] is True


def test_kr_financial_ratio_validation_roe_ok(monkeypatch):
    """계산 roe 가 stored 와 0.5% 이내 → ok."""
    values = {"IS_NI_PARENT": 19.0, "BS_EQ_PARENT:closing": 100.0, "BS_EQ_PARENT:opening": 100.0}
    monkeypatch.setattr(
        ontology_service, "build_ratio_values",
        lambda db, code, fs_div="CFS": (values, {"roe": 19.0}),
    )
    items = ontology_service.kr_financial_ratio_validation(None, "005930")
    # roe = 19/100 = 0.19 → ×100 = 19.0 vs stored 19.0 → diff 0
    assert items[0]["ok"] is True
    assert items[0]["reason"] == "ok"
    assert items[0]["calculated"] == pytest.approx(19.0)
