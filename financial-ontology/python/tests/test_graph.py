"""지표 그래프 순회 테스트."""

from __future__ import annotations

import pytest

from financial_ontology import Graph, get_ontology


@pytest.fixture(scope="module")
def graph():
    return Graph(get_ontology())


def test_ratio_inputs_roe(graph):
    assert set(graph.ratio_inputs("roe")) == {"IS_NI_PARENT", "BS_EQ_PARENT"}


def test_ratio_inputs_ebitda(graph):
    assert set(graph.ratio_inputs("ebitda_margin")) == {
        "IS_OP_INCOME",
        "IS_OPEX_DEPR",
        "IS_REV_TOTAL",
    }


def test_account_downstream_ratios(graph):
    ratios = graph.account_downstream_ratios("BS_CA_AR")
    assert "receivable_turnover" in ratios
    assert "cash_conversion_cycle" in ratios


def test_account_affects(graph):
    affects = graph.account_affects("BS_CA_AR")
    assert "WorkingCapital" in affects
    assert "OCF" in affects


def test_upstream_accounts(graph):
    # IS_GP_TOTAL depends_on IS_REV_TOTAL, IS_COGS_TOTAL
    assert set(graph.upstream_accounts("IS_GP_TOTAL")) >= {"IS_REV_TOTAL", "IS_COGS_TOTAL"}


def test_downstream_accounts(graph):
    children = graph.downstream_accounts("BS_CA_AR")
    assert set(children) == {"BS_CA_AR_GROSS", "BS_CA_AR_ALLOWANCE"}


def test_transitive_inputs(graph):
    # roe -> IS_NI_PARENT -> IS_PBT_TOTAL -> IS_OP_INCOME ... 재귀 전개
    inputs = set(graph.transitive_inputs("roe"))
    assert "IS_NI_PARENT" in inputs
    assert "IS_OP_INCOME" in inputs  # via IS_NI_TOTAL -> IS_PBT_TOTAL -> IS_OP_INCOME
    assert "IS_REV_TOTAL" in inputs


def test_ratios_depending_on_account(graph):
    using_total_ca = graph.ratios_depending_on("BS_CA_TOTAL")
    assert "current_ratio" in using_total_ca
    assert "quick_ratio" in using_total_ca
    using_cash = graph.ratios_depending_on("BS_CA_CASH")
    assert "cash_ratio" in using_cash


def test_account_to_ratios_index(graph):
    idx = graph.account_to_ratios_index()
    assert "BS_CA_TOTAL" in idx
    assert "current_ratio" in idx["BS_CA_TOTAL"]
    assert "cash_ratio" in idx["BS_CA_CASH"]
