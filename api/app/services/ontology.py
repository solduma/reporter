"""재무 온톨로지 서비스 — 라우터가 호출하는 응용 계층.

OntologyPort(get_ontology_port) 경유로 정규화·비율 계산을 수행한다. 계층 방향(routers → services →
adapters)을 지키기 위한 thin 서비스 — 온톨로지는 정적 데이터라 비즈니스 로직보다 경계 보장이 목적.
추후 기존 재무 흐름(financial_statement_rows 등)에 온톨로지 정규화를 끼워넣을 때 이 서비스를 경유.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.adapters.financial_ontology import get_ontology_port
from app.ports.financial_ontology import (
    AccountMeta,
    NormalizeResult,
    OntologyPort,
    RatioMeta,
    RatioResultOut,
)

if TYPE_CHECKING:
    from app.db.models import Financial, UsFinancial


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

# UsFinancial ORM 컬럼 → 온톨로지 정준 ID 매핑 메타(F1).
# SEC EDGAR companyfacts 로부터 도출된 US-GAAP 기반 지표를 동일한 ontology ID 체계로 정규화.
# 시가총액·주식수는 외부/주당 데이터로 ontology 계정에 직접 대응하지 않아 제외.
US_FINANCIAL_COLUMN_ONTOLOGY: dict[str, tuple[str, str]] = {
    "ttm_revenue": ("IS_REV_TOTAL", "account"),
    "ttm_net_income": ("IS_NI_PARENT", "account"),
    "ttm_operating_income": ("IS_OP_INCOME", "account"),
    "ttm_eps": ("eps", "ratio"),
    "equity": ("BS_EQ_PARENT", "account"),
    "per": ("per", "ratio"),
    "pbr": ("pbr", "ratio"),
    "psr": ("psr", "ratio"),
    "roe": ("roe", "ratio"),
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


def us_financial_ontology(row: UsFinancial) -> list[dict[str, object]]:
    """UsFinancial 행을 온톨로지 정준 ID/라벨/값으로 변환(F1).

    반환 항목: {key, ontology_id, kind, value, label, description}. ontology_id 는
    US-GAAP 계정명·비율과 동일한 KR/IFRS/US-GAAP 교차표준 ID. label·description 은
    온톨로지 메타에서 조회하며, 결측 시 key 를 label 로 fallback 한다.
    """
    from app.db.models import UsFinancial

    if not isinstance(row, UsFinancial):
        return []

    ont_ids = [ont_id for ont_id, _kind in US_FINANCIAL_COLUMN_ONTOLOGY.values()]
    label_map, _ = metric_info(ont_ids)
    info_by_id = {it["key"]: it for it in label_map}

    out: list[dict[str, object]] = []
    for col, (ont_id, kind) in US_FINANCIAL_COLUMN_ONTOLOGY.items():
        raw = getattr(row, col, None)
        value = float(raw) if raw is not None else None
        info = info_by_id.get(ont_id, {})
        out.append(
            {
                "key": col,
                "ontology_id": ont_id,
                "kind": kind,
                "value": value,
                "label": info.get("term") or col,
                "description": info.get("description"),
            }
        )
    return out


def us_financial_ratio_validation(row: UsFinancial) -> list[dict[str, object]]:
    """UsFinancial 저장값과 온톨로지 RatioEngine 계산값을 교차 검증(F2).

    US-GAAP TTM 값(USD)을 그대로 ontology 계정값으로 사용하고, market_cap/shares_outstanding
    을 외부 입력으로 넘겨 per/pbr/psr/roe/eps 를 재계산한다. 저장된 비율값과 비교해
    {ratio_id, stored, calculated, diff, ok, reason} 형태로 반환.
    """
    from app.db.models import UsFinancial

    if not isinstance(row, UsFinancial):
        return []

    values: dict[str, object] = {}
    for col, (ont_id, kind) in US_FINANCIAL_COLUMN_ONTOLOGY.items():
        if kind != "account":
            continue
        raw = getattr(row, col, None)
        if raw is not None:
            values[ont_id] = float(raw)

    if row.market_cap is not None:
        values["market_cap"] = float(row.market_cap)
    if row.shares is not None:
        values["shares_outstanding"] = float(row.shares)

    # 비교 대상이 되는 저장 비율 (eps/per/pbr/psr/roe).
    stored_ratios = {
        ratio_id: float(getattr(row, col, None))
        for col, (ratio_id, kind) in US_FINANCIAL_COLUMN_ONTOLOGY.items()
        if kind == "ratio" and getattr(row, col, None) is not None
    }

    ratio_ids = list(stored_ratios)
    if not ratio_ids:
        return []

    calculated = calculate_ratios(ratio_ids, values)
    results: list[dict[str, object]] = []
    for r in calculated:
        stored = stored_ratios.get(r.ratio_id)
        calc_val = float(r.value) if r.value is not None else None
        diff = None
        ok = False
        reason = r.reason
        if stored is not None and calc_val is not None:
            # 백분율(roe, eps 단위)과 배수(per/pbr/psr) 모두 허용 오차 0.5% 이내.
            denom = abs(stored) if stored != 0 else 1.0
            diff = round((calc_val - stored) / denom * 100, 2)
            ok = abs(diff) <= 0.5
            if not ok:
                reason = f"diff={diff}%"
        elif stored is None:
            reason = "no_stored"
            ok = True  # 저장값이 없으면 비교 불가, 계산 자체는 문제 없음.
        elif calc_val is None:
            reason = r.reason or "calc_missing"

        results.append(
            {
                "ratio_id": r.ratio_id,
                "stored": stored,
                "calculated": calc_val,
                "diff_pct": diff,
                "ok": ok,
                "reason": reason,
            }
        )
    return results


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


def transitive_inputs(ratio_id: str) -> list[str]:
    return _port().transitive_inputs(ratio_id)


def financial_metrics_meta(keys: list[str]) -> dict[str, dict[str, object]]:
    """Financial 시계열 키(revenue, per ...) → 온톨로지 정준 메타 + 관련 지표.

    account 종류는 파생 가능한 비율(related_ratios)을, ratio 종류는 필요 계정(related_accounts)을
    함께 반환해 LLM 이 수치의 정의·산출 근거를 볼 수 있게 한다(E1).
    """
    port = _port()
    ratio_map: dict[str, RatioMeta] | None = None
    out: dict[str, dict[str, object]] = {}

    def _ratio_map() -> dict[str, RatioMeta]:
        nonlocal ratio_map
        if ratio_map is None:
            ratio_map = {r.id: r for r in port.list_ratios()}
        return ratio_map

    for key in keys:
        entry = FINANCIAL_COLUMN_ONTOLOGY.get(key)
        ont_id: str | None = None
        kind: str | None = None
        if entry:
            ont_id, kind = entry
        else:
            if port.account(key) is not None:
                ont_id = key
                kind = "account"
            elif _ratio_map().get(key) is not None:
                ont_id = key
                kind = "ratio"

        if kind == "account":
            meta = port.account(ont_id) if ont_id else None
            if meta:
                out[key] = {
                    "ontology_id": meta.id,
                    "term": meta.korean_name,
                    "english_name": meta.english_name,
                    "description": meta.description,
                    "related_ratios": list(meta.ratios),
                }
        elif kind == "ratio":
            meta = _ratio_map().get(ont_id)
            if meta:
                out[key] = {
                    "ontology_id": meta.id,
                    "term": meta.name,
                    "korean_name": meta.korean_name,
                    "description": meta.description,
                    "unit": meta.unit,
                    "related_accounts": list(meta.required_accounts),
                }
    return out


def metric_info(keys: list[str]) -> tuple[list[dict[str, str | None]], float]:
    """Financial 컬럼 key / 온톨로지 account·ratio ID → 정준 라벨(term)·설명 조회.

    key 가 FINANCIAL_COLUMN_ONTOLOGY 에 있으면 매핑된 account/ratio 메타에서 조회한다.
    매핑에 없으면 key 자체를 온톨로지 account ID, 없으면 ratio ID 로 시도 — 이후 C3 에서
    비율 입력 계정 ID(`IS_NI_PARENT` 등) 라벨 조회에 재사용한다.
    coverage = description 확보된 key 비율.
    """
    port = _port()
    ratio_map: dict[str, RatioMeta] | None = None
    out: list[dict[str, str | None]] = []
    resolved = 0
    for key in keys:
        entry = FINANCIAL_COLUMN_ONTOLOGY.get(key)
        ont_id: str | None = None
        kind: str | None = None
        if entry:
            ont_id, kind = entry
        else:
            # key 자체가 account/ratio ID 인지 시도
            if port.account(key) is not None:
                ont_id = key
                kind = "account"
            else:
                if ratio_map is None:
                    ratio_map = {r.id: r for r in port.list_ratios()}
                if ratio_map.get(key) is not None:
                    ont_id = key
                    kind = "ratio"

        term: str | None = None
        desc: str | None = None
        if kind == "account":
            meta = port.account(ont_id) if ont_id else None
            term = meta.korean_name if meta else None
            desc = meta.description if meta else None
        elif kind == "ratio":
            if ratio_map is None:
                ratio_map = {r.id: r for r in port.list_ratios()}
            meta = ratio_map.get(ont_id) if ont_id else None
            term = meta.name if meta else None
            desc = meta.description if meta else None

        out.append({"key": key, "ontology_id": ont_id, "term": term, "description": desc})
        if desc is not None:
            resolved += 1
    coverage = resolved / len(keys) if keys else 0.0
    return out, coverage


def build_ratio_values(
    db: Session, code: str, fs_div: str = "CFS"
) -> tuple[dict[str, object], dict[str, float]]:
    """FinancialStatement JSONB + Financial 저장 계정값으로 RatioEngine 입력 구성(C1).

    - 최신 기간 = closing/suffix 없음 (BS 는 기말, IS/CF 는 당기 누적).
    - 직전 기간 = :prior / :opening (재무상태표 기초, 기타 기간 비교용).
    amount 는 DART 원문이 문자열일 수 있어 float 변환; 실패 항목은 스킵.

    Financial 저장 계정값(revenue, operating_income, net_income, depreciation, capex)은
    재무제표 JSONB 와 단위(억원)가 동일하므로 병합해 RatioEngine 입력을 보강한다.
    저장 비율값(per/pbr/roe 등)은 RatioEngine 이 시장데이터를 요구해 직접 계산하지 못하므로
    별도 dict 로 반환; company_ratios() 에서 fallback 으로 사용한다.
    """
    # 지연 import — services ↔ company_service 순환 방지.
    from app.services.company_service import financial_statement_rows, latest_valuation

    values: dict[str, object] = {}
    statements = financial_statement_rows(db, code, fs_div=fs_div)
    if statements:
        statements.sort(key=lambda s: s.period)
        current = statements[-1]
        prior = statements[-2] if len(statements) >= 2 else None
        for stmt, suffix in (
            (current, ""),
            (prior, ":prior") if prior else (None, ""),
            (prior, ":opening") if prior else (None, ""),
        ):
            if stmt is None:
                continue
            for section_items in stmt.data.values():
                for item in section_items:
                    ont_id = item.get("ontology_id")
                    amount = item.get("amount")
                    if ont_id is None or amount is None:
                        continue
                    try:
                        values[f"{ont_id}{suffix}"] = float(amount)
                    except (TypeError, ValueError):
                        continue

    stored_accounts: dict[str, float] = {}
    stored_ratios: dict[str, float] = {}
    fin = latest_valuation(db, code, fs_div=fs_div)
    if fin is not None:
        stored_accounts = financial_row_to_ontology_values(fin)
        stored_ratios = financial_row_stored_ratios(fin)
        values.update(stored_accounts)

    return values, stored_ratios


_INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bank": ("은행", "bank"),
    "insurance": ("보험", "insurance"),
    "securities": ("증권", "securities", "브로커"),
}


def _detect_financial_industry(themes: list[str]) -> str | None:
    """judal 테마명에서 온톨로지 산업 태그(bank/insurance/securities)를 감지."""
    lowered = [t.lower() for t in themes]
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(kw in theme for theme in lowered for kw in keywords):
            return industry
    return None


def company_ratios(db: Session, code: str, fs_div: str = "CFS", industry: str | None = None) -> list[RatioResultOut]:
    """종목의 최신 재무제표 기준 온톨로지 비율 일괄 계산(C1+E3).

    산업 태그(bank/insurance/securities)가 있으면 해당 산업 확장 비율 + 공통 비율만 계산.
    미지정 시 종목의 judal 테마명으로 자동 감지. 태그 없는 비율은 항상 포함.
    PER/PBR/PSR/EV/EBITDA/ROE 등 시장데이터 기반 비율은 Financial 저장값이 있으면
    fallback 으로 채워 반환한다.
    """
    port = _port()
    if industry is None:
        # 지연 import — services ↔ company_service 순환 방지.
        from app.services.company_service import theme_names

        industry = _detect_financial_industry(theme_names(db, code))
    ratio_ids = [
        r.id
        for r in port.list_ratios()
        if industry is None or not r.tags or industry in r.tags
    ]
    values, stored = build_ratio_values(db, code, fs_div=fs_div)
    if not values and fs_div == "CFS":
        # 연결 재무제표가 없으면 별도 재무제표로 폴백(기존 latest_valuation 동작과 동일).
        values, stored = build_ratio_values(db, code, fs_div="OFS")
    results = port.calculate_many(ratio_ids, values)
    return [
        (
            replace(
                r,
                value=Decimal(stored[r.ratio_id]),
                ok=True,
                reason="stored_fallback",
                missing=[],
            )
            if r.value is None and r.ratio_id in stored
            else r
        )
        for r in results
    ]
    """종목의 최신 재무제표 기준 57개 온톨로지 비율 일괄 계산(C1).

    PER/PBR/PSR/EV/EBITDA/ROE 등 시장데이터 기반 비율은 Financial 저장값이 있으면
    fallback 으로 채워 반환한다(온톨로지 계산값과 저장값의 차이는 warnings/reason 에
    노출되지 않고 저장값 우선).
    """
    port = _port()
    ratio_ids = [r.id for r in port.list_ratios()]
    values, stored = build_ratio_values(db, code, fs_div=fs_div)
    if not values and fs_div == "CFS":
        # 연결 재무제표가 없으면 별도 재무제표로 폴백(기존 latest_valuation 동작과 동일).
        values, stored = build_ratio_values(db, code, fs_div="OFS")
    results = port.calculate_many(ratio_ids, values)
    return [
        (
            replace(
                r,
                value=Decimal(stored[r.ratio_id]),
                ok=True,
                reason="stored_fallback",
                missing=[],
            )
            if r.value is None and r.ratio_id in stored
            else r
        )
        for r in results
    ]
