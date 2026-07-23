"""재무 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

OntologyPort(get_ontology_port) 경유로 정규화·비율 계산을 수행한다. 계층 방향(routers → services →
adapters)을 지키기 위한 thin 서비스 — 온톨로지는 정적 데이터라 비즈니스 로직보다 경계 보장이 목적.
추후 기존 재무 흐름(financial_statement_rows 등)에 온톨로지 정규화를 끼워넣을 때 이 서비스를 경유.
"""

from __future__ import annotations

from app.adapters.financial_ontology import get_ontology_port
from app.ports.financial_ontology import (
    AccountMeta,
    NormalizeResult,
    OntologyPort,
    RatioMeta,
    RatioResultOut,
)


def _port() -> OntologyPort:
    return get_ontology_port()


def normalize(terms: list[str], standard: str | None = None) -> list[NormalizeResult]:
    return _port().resolve_many(terms, standard=standard)


def enrich_with_ontology_id(statements: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """재무제표 항목(dict)에 name 정규화 결과 ontology_id 를 주입(인플레이스 mutating).

    수집(writer) 단계에서 호출해 FinancialStatement JSONB 에 ontology_id 를 영속화한다.
    응답 단(companies.py:_build_items)은 영속화된 값을 우선 사용하고, 구버전 행(미보관)은
    동적 정규화 fallback 한다. 항목 순서 보존 — names 수집과 id 대입을 동일 순회 순서로 수행.
    """
    names: list[str] = []
    for items in statements.values():
        for item in items:
            names.append(item.get("name", "") or "")
    if not names:
        return statements
    ont_ids = [r.id for r in normalize(names)]
    idx = 0
    for items in statements.values():
        for item in items:
            item["ontology_id"] = ont_ids[idx] if names[idx] else None
            idx += 1
    return statements


def calculate_one(ratio_id: str, values: dict[str, object]) -> RatioResultOut:
    return _port().calculate(ratio_id, values)


def calculate_ratios(ratio_ids: list[str], values: dict[str, object]) -> list[RatioResultOut]:
    return _port().calculate_many(ratio_ids, values)


def required_accounts(ratio_id: str) -> list[str]:
    return _port().required(ratio_id)


def ratios(category: str | None = None) -> list[RatioMeta]:
    return _port().list_ratios(category=category)


def accounts(statement: str | None = None) -> list[AccountMeta]:
    return _port().list_accounts(statement=statement)


def account(account_id: str) -> AccountMeta | None:
    return _port().account(account_id)
