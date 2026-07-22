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
