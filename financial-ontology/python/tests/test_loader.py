"""온톨로지 로더·스키마 검증·인덱스 구축 테스트."""

from __future__ import annotations

import pytest

from financial_ontology import get_ontology, load_ontology


@pytest.fixture(scope="module")
def ont():
    return get_ontology()


def test_loads_all_accounts_and_statements(ont):
    assert len(ont.accounts) == 190
    assert len(ont.statements) == 5
    assert {
        "balance_sheet",
        "income_statement",
        "comprehensive_income",
        "changes_in_equity",
        "cash_flow",
    } <= {s["type"] for s in ont.statements.values()}


def test_schema_validation_passes_by_default():
    # validate=True(기본)가 스키마 검증을 통과해야 함 — 예외 없이 로드
    ont = load_ontology(validate=True)
    assert ont.account("BS_CA_CASH") is not None


def test_known_accounts_present(ont):
    for aid in [
        "BS_CA_CASH",
        "BS_CA_AR",
        "IS_OP_INCOME",
        "CF_OP_TOTAL",
        "BS_BANK_DEPOSIT",
        "IS_INS_PREMIUM",
        "BS_SEC_TRADING_SEC",
    ]:
        assert aid in ont.accounts, aid


def test_taxonomy_index_dart(ont):
    assert ont.by_taxonomy["dart"]["ifrs-full_CashAndCashEquivalents"] == "BS_CA_CASH"
    assert ont.by_taxonomy["dart"]["ifrs-full_TradeAndOtherCurrentReceivables"] == "BS_CA_AR"


def test_korean_name_index(ont):
    assert ont.by_korean_name["매출채권"] == "BS_CA_AR"
    assert ont.by_korean_name["영업이익"] == "IS_OP_INCOME"


def test_english_name_and_alias_index(ont):
    assert ont.by_english_name["Cash and Cash Equivalents"] == "BS_CA_CASH"
    assert ont.by_alias["외상매출금"] == "BS_CA_AR"
    assert ont.by_alias["Trade Receivables"] == "BS_CA_AR"


def test_ratios_loaded(ont):
    assert len(ont.ratios) == 57
    for rid in ["roe", "current_ratio", "ebitda_margin", "nim", "combined_ratio", "evebitda"]:
        assert rid in ont.ratios, rid


def test_account_hierarchy_integrity(ont):
    # 모든 parent/children 참조가 실제 계정을 가리킴
    ids = ont.account_ids
    for acc in ont.accounts.values():
        assert acc.parent is None or acc.parent in ids
        for c in acc.children:
            assert c in ids
