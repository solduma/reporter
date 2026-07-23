"""재무 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

OntologyPort(get_ontology_port) 경유로 정규화·비율 계산을 수행한다. 계층 방향(routers → services →
adapters)을 지키기 위한 thin 서비스 — 온톨로지는 정적 데이터라 비즈니스 로직보다 경계 보장이 목적.
추후 기존 재무 흐름(financial_statement_rows 등)에 온톨로지 정규화를 끼워넣을 때 이 서비스를 경유.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.financial_ontology import get_ontology_port
from app.ports.financial_ontology import (
    AccountMeta,
    NormalizeResult,
    OntologyPort,
    RatioMeta,
    RatioResultOut,
)

if TYPE_CHECKING:
    from app.db.models import Financial


# Financial ORM 컬럼 → 온톨로지 정준 ID 매핑 메타(A3).
# - kind="account": 계정 값(억원/원). RatioEngine 입력({ontology_id: value})으로 사용.
# - kind="ratio":  이미 계산된 비율값(소수/배/%). C2 정합(저장값 vs 온톨로지 계산값)용.
# 매핑 누락 컬럼(dps·ebitda·net_debt·effective_tax_rate·cost_of_debt)은 파생/주당 지표로
# 온톨로지 계정·비율에 직접 대응하지 않는다(별도 처리).
FINANCIAL_COLUMN_ONTOLOGY: dict[str, tuple[str, str]] = {
    "revenue": ("IS_REV_TOTAL", "account"),
    "operating_income": ("IS_OP_INCOME", "account"),
    "net_income": ("IS_NI_PARENT", "account"),
    "depreciation": ("CF_OP_DEPR", "account"),
    # capex 컬럼은 PPE+무형자산 취득 합산이나 온톨로지 FCF 비율 입력은 CF_INV_PPE.
    "capex": ("CF_INV_PPE", "account"),
    "eps": ("eps", "ratio"),
    "bps": ("bvps", "ratio"),
    "per": ("per", "ratio"),
    "pbr": ("pbr", "ratio"),
    "roe": ("roe", "ratio"),
    "psr": ("psr", "ratio"),
    "ev_ebitda": ("evebitda", "ratio"),
    "div_yield": ("dividend_yield", "ratio"),
}


def financial_row_to_ontology_values(row: Financial) -> dict[str, float]:
    """Financial 행의 계정 종류 컬럼을 {ontology_id: value} 로 변환(RatioEngine 입력용).

    ratio 종류 컬럼(이미 계산된 비율)은 제외 — RatioEngine 입력이 아닌 비교 대상.
    None 값은 스킵(결측). 단위는 컬럼 원단위(억원 등) 그대로 — 비율은 단위 무관, 금액 비율은
    동일 단위 입력 전제.
    """
    values: dict[str, float] = {}
    for col, (ont_id, kind) in FINANCIAL_COLUMN_ONTOLOGY.items():
        if kind != "account":
            continue
        v = getattr(row, col, None)
        if v is not None:
            values[ont_id] = float(v)
    return values


def financial_row_stored_ratios(row: Financial) -> dict[str, float]:
    """Financial 행의 비율 종류 컬럼(이미 계산된 값)을 {ratio_id: value} 로 반환(C2 정합용)."""
    values: dict[str, float] = {}
    for col, (ratio_id, kind) in FINANCIAL_COLUMN_ONTOLOGY.items():
        if kind != "ratio":
            continue
        v = getattr(row, col, None)
        if v is not None:
            values[ratio_id] = float(v)
    return values


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


def metric_info(keys: list[str]) -> tuple[list[dict[str, str | None]], float]:
    """Financial 컬럼 key → 온톨로지 정준 라벨(term)·설명(description) 조회(B1 라벨 단일 출처).

    key 가 FINANCIAL_COLUMN_ONTOLOGY 에 있으면 해당 account/ratio 메타에서 term·description
    을 가져온다. 없거나 온톨로지에 미매칭이면 null. coverage = description 확보된 key 비율.
    """
    port = _port()
    ratio_map: dict[str, RatioMeta] | None = None
    out: list[dict[str, str | None]] = []
    resolved = 0
    for key in keys:
        entry = FINANCIAL_COLUMN_ONTOLOGY.get(key)
        if not entry:
            out.append({"key": key, "ontology_id": None, "term": None, "description": None})
            continue
        ont_id, kind = entry
        if kind == "account":
            meta = port.account(ont_id)
            term = meta.korean_name if meta else None
            desc = meta.description if meta else None
        else:  # ratio
            if ratio_map is None:
                ratio_map = {r.id: r for r in port.list_ratios()}
            meta = ratio_map.get(ont_id)
            term = meta.name if meta else None
            desc = meta.description if meta else None
        out.append({"key": key, "ontology_id": ont_id, "term": term, "description": desc})
        if desc is not None:
            resolved += 1
    coverage = resolved / len(keys) if keys else 0.0
    return out, coverage
