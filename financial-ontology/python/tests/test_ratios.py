"""비율 계산 엔진 테스트."""

from __future__ import annotations

from decimal import Decimal

import pytest

from financial_ontology import RatioEngine, get_ontology


@pytest.fixture(scope="module")
def engine():
    return RatioEngine(get_ontology())


def test_current_ratio(engine):
    r = engine.calculate("current_ratio", {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 60})
    assert r.ok
    assert r.value == Decimal(100) / Decimal(60)


def test_quick_ratio(engine):
    r = engine.calculate(
        "quick_ratio",
        {"BS_CA_TOTAL": 100, "BS_CA_INV": 20, "BS_CA_PREPAID": 5, "BS_CL_TOTAL": 50},
    )
    assert r.ok
    assert r.value == Decimal(75) / Decimal(50)


def test_roe_with_period_average(engine):
    r = engine.calculate(
        "roe",
        {"IS_NI_PARENT": 100, "BS_EQ_PARENT:opening": 400, "BS_EQ_PARENT:closing": 600},
    )
    assert r.ok, r.reason
    assert r.value == Decimal("0.2")  # 100 / ((400+600)/2) = 0.2


def test_roe_plain_value_fallback(engine):
    # opening 미제공 → closing(본 ID) 사용 경고, 평균 미적용
    r = engine.calculate("roe", {"IS_NI_PARENT": 100, "BS_EQ_PARENT": 500})
    assert r.ok
    assert r.value == Decimal(100) / Decimal(500)
    assert any("평균" in w for w in r.warnings)


def test_ebitda_margin(engine):
    r = engine.calculate(
        "ebitda_margin",
        {"IS_OP_INCOME": 60, "IS_OPEX_DEPR": 20, "IS_REV_TOTAL": 200},
    )
    assert r.ok
    assert r.value == Decimal(80) / Decimal(200)


def test_missing_required_account(engine):
    r = engine.calculate("current_ratio", {"BS_CA_TOTAL": 100})  # BS_CL_TOTAL 결측
    assert not r.ok
    assert "missing" in r.reason
    assert "BS_CL_TOTAL" in r.missing


def test_division_by_zero(engine):
    r = engine.calculate("current_ratio", {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 0})
    assert not r.ok
    assert r.reason == "divzero_or_unresolved"


def test_external_input_eps(engine):
    r = engine.calculate("eps", {"IS_NI_PARENT": 1000, "shares_outstanding": 200})
    assert r.ok, r.reason
    assert r.value == Decimal(5)


def test_abs_in_formula_capex_to_sales(engine):
    r = engine.calculate("capex_to_sales", {"CF_INV_PPE_ACQ": -50, "IS_REV_TOTAL": 1000})
    assert r.ok, r.reason
    assert r.value == Decimal("0.05")


def test_composite_ratio_unsupported(engine):
    # retention_ratio: "1 - 배당성향" — 타 비율 참조(한글) → 자동 평가 불가
    r = engine.calculate("retention_ratio", {"dividend_payout": Decimal("0.3")})
    assert not r.ok
    assert r.reason.startswith("composite_or_manual")


def test_unknown_ratio(engine):
    r = engine.calculate("no_such_ratio", {})
    assert not r.ok
    assert "unknown ratio" in r.reason


def test_required_returns_accounts(engine):
    req = engine.required("ebitda_margin")
    assert set(req) == {"IS_OP_INCOME", "IS_OPEX_DEPR", "IS_REV_TOTAL"}
