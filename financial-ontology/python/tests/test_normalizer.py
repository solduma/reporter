"""계정 정규화기 테스트."""

from __future__ import annotations

import pytest

from financial_ontology import Normalizer, get_ontology


@pytest.fixture(scope="module")
def normalizer():
    return Normalizer(get_ontology())


def test_resolve_existing_ontology_id(normalizer):
    assert normalizer.resolve("BS_CA_AR") == "BS_CA_AR"


def test_resolve_korean_canonical_name(normalizer):
    assert normalizer.resolve("매출채권") == "BS_CA_AR"
    assert normalizer.resolve("영업이익") == "IS_OP_INCOME"


def test_resolve_english_name(normalizer):
    assert normalizer.resolve("Cash and Cash Equivalents") == "BS_CA_CASH"
    assert normalizer.resolve("Accounts Receivable, Net") == "BS_CA_AR"


def test_resolve_alias(normalizer):
    assert normalizer.resolve("외상매출금") == "BS_CA_AR"
    assert normalizer.resolve("현금예금") == "BS_CA_CASH"


def test_resolve_dart_taxonomy_with_prefix(normalizer):
    assert normalizer.resolve("ifrs-full_CashAndCashEquivalents", standard="dart") == "BS_CA_CASH"


def test_resolve_dart_taxonomy_without_prefix(normalizer):
    # 접두 없이 들어와도 dart 보정
    assert normalizer.resolve("CashAndCashEquivalents", standard="dart") == "BS_CA_CASH"


def test_resolve_unknown_returns_none(normalizer):
    r = normalizer.resolve_detail("존재안하는계정명")
    assert r.id is None
    assert not r.resolved


def test_resolve_many_and_coverage(normalizer):
    terms = ["매출채권", "현금및현금성자산", "없는항목", "영업이익"]
    results = normalizer.resolve_many(terms)
    assert [r.id for r in results] == ["BS_CA_AR", "BS_CA_CASH", None, "IS_OP_INCOME"]
    assert normalizer.coverage(terms) == 0.75


def test_resolve_empty(normalizer):
    assert normalizer.resolve("") is None


def test_resolve_strips_parenthetical_suffix(normalizer):
    # DART 공시명은 정준명에 괄호 접미사를 붙여 내는 경우가 많다.
    assert normalizer.resolve("영업이익(손실)") == "IS_OP_INCOME"
    assert normalizer.resolve("당기순이익(손실)") == "IS_NI_TOTAL"
    assert normalizer.resolve("법인세비용(수익)") == "IS_TAX_TOTAL"
    r = normalizer.resolve_detail("영업이익(손실)")
    assert r.id == "IS_OP_INCOME"
    assert r.matched_via == "korean_name"


def test_resolve_fullwidth_paren(normalizer):
    # 전각 괄호(（）)도 처리 — 일부 공시 텍기에 섞임.
    assert normalizer.resolve("영업이익（손실）") == "IS_OP_INCOME"
