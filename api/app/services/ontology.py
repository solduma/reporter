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
from app.domain import financials
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


def us_financial_raw_ontology(row: UsFinancial) -> list[dict[str, object]]:
    """UsFinancial.raw_ontology JSONB 를 정규화된 원시 항목 리스트로 반환(F3b).

    DB 에 영속된 값을 그대로 반환하며, None 이면 빈 리스트. 항목은
    {ontology_id, label, taxonomy_concept, namespace, unit, period_end,
    period_start, value} 형태.
    """
    from app.db.models import UsFinancial

    if not isinstance(row, UsFinancial):
        return []
    return row.raw_ontology or []


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


def kr_financial_ratio_validation(
    db: Session, code: str, fs_div: str = "CFS"
) -> list[dict[str, object]]:
    """Financial 저장 비율 vs 온톨로지 RatioEngine 계산값(연환산) 교차 검증(C2, US 와 대칭).

    저장 비율 중 build_ratio_values 출력만으로 계산 가능한 것(roe 등)은 0.5% 허용오차로 비교.
    시장데이터(market_cap/shares/EV)가 필요한 per/pbr/psr/evebitda/eps/bvps/dividend_yield 는
    reason="no_market_data" ok=True(비교 불가, 무결점). 노출 전용 — 점수 반영 X.
    """
    port = _port()
    values, stored = build_ratio_values(db, code, fs_div=fs_div)
    if not values and fs_div == "CFS":
        values, stored = build_ratio_values(db, code, fs_div="OFS")
    if not stored:
        return []
    unit_map = {m.id: m.unit for m in port.list_ratios()}
    calculated = {r.ratio_id: r for r in port.calculate_many(list(stored), values)}
    results: list[dict[str, object]] = []
    for ratio_id, stored_val in stored.items():
        calc = calculated.get(ratio_id)
        calc_val = float(calc.value) if (calc and calc.value is not None) else None
        # percentage 비율은 엔진이 분수로 반환 → 저장(퍼센트)과 비교 위해 ×100.
        if calc_val is not None and unit_map.get(ratio_id) == "percentage":
            calc_val = calc_val * 100
        diff: float | None = None
        ok = False
        if calc_val is None:
            reason = "no_market_data"
            ok = True  # 엔진 입력(시장데이터) 부족 → 비교 불가, 계산 자체는 무결점.
        else:
            denom = abs(stored_val) if stored_val else 1.0
            diff = round((calc_val - stored_val) / denom * 100, 2)
            ok = abs(diff) <= 0.5
            reason = "ok" if ok else f"diff={diff}%"
        results.append(
            {
                "ratio_id": ratio_id,
                "stored": stored_val,
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

    Korean name 정규화 실패 시 DART account_id (taxonomy) 로 fallback 하여
    ifrs-full_* namespace 항목을 온톨로지에 매핑한다.
    수집(writer) 단계에서 호출해 FinancialStatement JSONB 에 ontology_id 를 영속화한다.
    응답 단(companies.py:_build_items)은 영속화된 값을 우선 사용하고, 구버전 행(미보관)은
    동적 정규화 fallback 한다. 항목 순서 보존.
    """
    items_flat: list[tuple[dict, str, str]] = []  # (item, name, account_id)
    for items in statements.values():
        for item in items:
            items_flat.append((item, item.get("name", "") or "", item.get("account_id", "") or ""))

    if not items_flat:
        return statements

    # 1차: name 정규화
    names = [name for _, name, _ in items_flat]
    ont_ids = [r.id for r in normalize(names)]

    # 2차: name 실패 항목 → account_id(taxonomy) 정규화
    none_indices = {i for i, oid in enumerate(ont_ids) if oid is None}
    if none_indices:
        fallback_terms = [items_flat[i][2] for i in none_indices]  # account_id
        fallback_ids = [r.id for r in normalize(fallback_terms, standard="dart")]
        for count, i in enumerate(none_indices):
            ont_ids[i] = fallback_ids[count]

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


def _period_to_yq(period: str) -> tuple[int, int]:
    """'2026.03' → (2026, 1). .03/.06/.09/.12 = Q1/Q2/Q3/Q4."""
    year, mm = period.split(".")
    return (int(year), {3: 1, 6: 2, 9: 3, 12: 4}[int(mm)])


def _statement_for_yq(statements: list, target_yq: tuple[int, int]):
    for s in statements:
        if _period_to_yq(s.period) == target_yq:
            return s
    return None


def _ttm_value(
    raw: dict[tuple[int, int], float], latest_yq: tuple[int, int]
) -> float | None:
    """DART 원시 시계열(원, Q4=연간누적)에서 latest_yq 기준 TTM(4분기 합).

    4분기가 불충족하면(신규 상장 등) 최신 연간(.12 누적) 원시값 fallback — 연간 누적은
    이미 1년치라 연환산과 동일 의미. 그것도 없으면 None(비율은 결측 처리).
    """
    discrete = {yq: financials.discrete_quarter(raw, yq) for yq in raw}
    ttm = financials.ttm_from_discrete(discrete, latest_yq)
    if ttm is not None:
        return ttm
    # 4분기 불충족 시 latest_yq 연도 연간(.12) 원시값 fallback
    annual = raw.get((latest_yq[0], 4))
    return annual if annual is not None else None


def build_ratio_values(
    db: Session, code: str, fs_div: str = "CFS"
) -> tuple[dict[str, object], dict[str, float]]:
    """FinancialStatement JSONB 로 RatioEngine 입력 구성(C1+C2 연환산).

    - IS/CF(흐름) 계정: 최신 4분기를 discrete_quarter(Q4=연간-누적) 후 TTM 합산 → bare namespace.
      4분기 불충족 시 최신 연간(.12) fallback. 단위는 JSONB 원으로 일관.
    - BS(재고) 계정: bare=최신 closing, `:opening`=1년 전(4분기 전) closing → `_평균`/`_기초`
      비율(roa/asset_turnover)이 연환산 분자와 대응.
    amount 는 DART 원문이 문자열일 수 있어 float 변환; 실패 항목은 스킵.

    영속화된 ontology_id 가 없는 항목(A1 백필 누락·신규 별칭)은 normalizer 로 동적 부여한다.
    DART 가 소계정 행만 저장하는 최상위 총계(자산·부채·자본총계)는 ontology account formula 로
    합산해 채운다(debt_ratio 등). Financial 저장 계정값(억원)은 단위 불일치를 일으키므로
    결측 보강(fallback-only)으로만 쓰고 JSONB 원 값을 덮어쓰지 않는다(gross_margin 정합).
    저장 비율값(per/pbr/roe 등)은 시장데이터가 필요해 직접 계산 불가 — 별도 dict 로 반환.
    """
    # 지연 import — services ↔ company_service 순환 방지.
    from app.services.company_service import financial_statement_rows, latest_valuation

    port = _port()
    values: dict[str, object] = {}
    statements = financial_statement_rows(db, code, fs_div=fs_div)
    if statements:
        statements.sort(key=lambda s: s.period)
        latest = statements[-1]
        latest_yq = _period_to_yq(latest.period)
        # BS 기초 = 1년 전(4분기 전) statement. `_평균`/`_기초` 비율의 분모.
        opening_yq = latest_yq
        for _ in range(4):
            opening_yq = financials.prev_yq(opening_yq)
        opening_stmt = _statement_for_yq(statements, opening_yq)

        raw_series: dict[str, dict[tuple[int, int], float]] = {}
        bs_closing: dict[str, float] = {}
        bs_opening: dict[str, float] = {}

        for stmt in statements:
            yq = _period_to_yq(stmt.period)
            is_closing = stmt is latest
            is_opening = stmt is opening_stmt
            for section_items in stmt.data.values():
                for item in section_items:
                    ont_id = item.get("ontology_id")
                    amount = item.get("amount")
                    if amount is None:
                        continue
                    # 미영속화 항목은 normalizer 로 정준 ID 동적 부여(백필 없이 기존 행 지원).
                    # normalizer 내부 캐시 처리하므로 port.account 별도 캐시 불필요.
                    # name 정규화 실패 시 account_id(taxonomy) 로 fallback.
                    if ont_id is None and item.get("name", ""):
                        r = port.resolve(item["name"])
                        ont_id = r.id  # id=None 이면 매핑 실패 → account_id 시도
                    if ont_id is None and item.get("account_id", ""):
                        r = port.resolve(item["account_id"], standard="dart")
                        ont_id = r.id
                    if ont_id is None:
                        continue
                    try:
                        amt = float(amount)
                    except (TypeError, ValueError):
                        continue
                    meta = port.account(ont_id)
                    if meta is None:
                        continue
                    statements_of = meta.statement
                    if "balance_sheet" in statements_of:
                        # 원 단위 그대로 — aggregate computation 은 원 값으로 수행(debt_ratio 등 비율 정합).
                        if is_closing:
                            bs_closing[ont_id] = amt
                        if is_opening:
                            bs_opening[ont_id] = amt
                    elif "income_statement" in statements_of or "cash_flow" in statements_of:
                        raw_series.setdefault(ont_id, {})[yq] = amt  # 원 단위 — _ttm_value에서 ÷1e8

        for ont_id, amt in bs_closing.items():
            values[ont_id] = amt  # bare: aggregate computation + ratio _기초/_기말 sanitization fallback
            values[f"{ont_id}:closing"] = amt  # :closing: ratio engine _기말 sanitization
        for ont_id, amt in bs_opening.items():
            values[f"{ont_id}:opening"] = amt
        for ont_id, raw in raw_series.items():
            ttm = _ttm_value(raw, latest_yq)
            if ttm is not None:
                values[ont_id] = ttm

    stored_ratios: dict[str, float] = {}
    fin = latest_valuation(db, code, fs_div=fs_div)
    if fin is not None:
        stored_ratios = financial_row_stored_ratios(fin)

    _compute_aggregate_accounts(port, values)

    return values, stored_ratios


def _values_for_suffix(values: dict[str, object], suffix: str) -> dict[str, object]:
    """접미별 leaf namespace — "" 는 colon 없는 키, ":prior"/":opening" 은 해당 접미 키에서 접미 제거."""
    if not suffix:
        return {k: v for k, v in values.items() if ":" not in k}
    n = len(suffix)
    return {k[:-n]: v for k, v in values.items() if k.endswith(suffix)}


def _compute_aggregate_accounts(port: OntologyPort, values: dict[str, object]) -> None:
    """온톨로지 account formula 로 집계(총계) 계정을 채운다.

    DART 재무제표 JSONB 는 소계정 행만 저장하고 자산총계·부채총계·자본총계 최상위 총계 행은
    주지 않는다. /financial-statements 의 _add_calculated_totals 가 표시용으로 합산하는 것과
    동일한 총계를 RatioEngine 입력에도 넣어 debt_ratio/equity_ratio/asset_turnover 가 계산되게 한다.
    직접 저장된 값(leaf·동적 정규화값)은 덮어쓰지 않고, formula 평가로 결정된 미결측 총계만 추가.
    기간 접미("", :prior, :opening)별로 독립 평가하며, 의존 체인(BS_EQ_TOTAL ← BS_EQ_PARENT)은
    위상 정렬 대신 고정 반복(계정 수+1회)으로 수렴시킨다.
    """
    formula_accounts = [a for a in port.list_accounts() if a.formula]
    if not formula_accounts:
        return
    suffixes = ("", ":prior", ":opening")
    for _ in range(len(formula_accounts) + 1):
        changed = False
        for suffix in suffixes:
            ns = _values_for_suffix(values, suffix)
            for acc in formula_accounts:
                key = f"{acc.id}{suffix}"
                if key in values:
                    continue
                res = port.compute_account(acc.id, ns)
                if res.ok and res.value is not None:
                    values[key] = float(res.value)
                    changed = True
        if not changed:
            break


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


def _is_flow_stock_ratio(port: OntologyPort, ratio_id: str) -> bool:
    """BS 재고(기말 시점값)와 IS/CF 흐름(기간 누적값)을 섞는 비율 여부.

    이런 비율(ROE·ROA·asset_turnover·재고/매출채권 회전율)은 IS/CF 가 연환산(TTM)되어야
    정확하다. build_ratio_values 가 IS/CF 를 TTM 합산하므로 이제 정확히 계산된다.
    """
    req = port.required(ratio_id)
    has_stock = any(a.startswith("BS_") for a in req)
    has_flow = any(a.startswith("IS_") or a.startswith("CF_") for a in req)
    return has_stock and has_flow


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
    unit_map = {m.id: m.unit for m in port.list_ratios()}
    out: list[RatioResultOut] = []
    for r in results:
        # 큐레이션된 저장 비율(per/pbr/roe/psr/evebitda/bvps/eps)은 엔진이 계산 가능해져도
        # 저장값을 우선한다 — DART 공시값이 권위있고, 시장데이터(PER/PBR)는 엔진이 계산 못 함.
        if r.ratio_id in stored:
            out.append(
                replace(r, value=Decimal(stored[r.ratio_id]), ok=True, reason="stored", missing=[])
            )
            continue
        # 엔진은 percentage 비율을 분수(0.05)로 반환. 저장 비율은 퍼센트(5.0) 단위이므로
        # 계산값도 ×100 환산해 표시·정합 단위를 맞춘다(operating_margin·roa·gross_margin 정합).
        if r.value is not None and unit_map.get(r.ratio_id) == "percentage":
            out.append(replace(r, value=Decimal(r.value) * 100))
            continue
        out.append(r)
    return out
