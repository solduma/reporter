"""OntologyPort 구현 — financial_ontology 패키지 래핑.

financial_ontology.get_ontology() 가 온톨로지 YAML 을 로드·스키마 검증해 캐시(프로세스 단일).
여기서만 financial_ontology 패키지를 직접 import 한다(import-linter ontology-behind-port).
패키지 객체(Resolution/RatioResult/Account/Ratio)를 포트 DTO 로 변환해 반환.
"""

from __future__ import annotations

from app.ports.financial_ontology import (
    AccountMeta,
    NormalizeResult,
    RatioMeta,
    RatioResultOut,
)
from financial_ontology import (
    Account,
    Normalizer,
    Ontology,
    Ratio,
    RatioEngine,
    RatioResult,
    get_ontology,
)

_ONT: Ontology | None = None
_NORMALIZER: Normalizer | None = None
_ENGINE: RatioEngine | None = None


def _ensure() -> tuple[Ontology, Normalizer, RatioEngine]:
    """온톨로지·정규화기·비율엔진을 지연 1회 로드(모듈 전역 캐시)."""
    global _ONT, _NORMALIZER, _ENGINE
    if _ONT is None:
        _ONT = get_ontology()  # 스키마 검증 포함, lru_cache 내부 캐시
        _NORMALIZER = Normalizer(_ONT)
        _ENGINE = RatioEngine(_ONT)
    assert _ONT is not None and _NORMALIZER is not None and _ENGINE is not None
    return _ONT, _NORMALIZER, _ENGINE


class OntologyAdapter:
    """OntologyPort 구현. 상태 없는 thin wrapper(온톨로지는 정적 데이터)."""

    def resolve(self, term: str, standard: str | None = None) -> NormalizeResult:
        _ont, norm, _eng = _ensure()
        r = norm.resolve_detail(term, standard=standard)
        return NormalizeResult(term=r.term, id=r.id, matched_via=r.matched_via)

    def resolve_many(self, terms: list[str], standard: str | None = None) -> list[NormalizeResult]:
        _ont, norm, _eng = _ensure()
        return [
            NormalizeResult(term=r.term, id=r.id, matched_via=r.matched_via)
            for r in norm.resolve_many(terms, standard=standard)
        ]

    def calculate(self, ratio_id: str, values: dict[str, object]) -> RatioResultOut:
        _ont, _norm, eng = _ensure()
        return _to_out(eng.calculate(ratio_id, values))

    def calculate_many(
        self, ratio_ids: list[str], values: dict[str, object]
    ) -> list[RatioResultOut]:
        _ont, _norm, eng = _ensure()
        return [_to_out(r) for r in eng.calculate_many(ratio_ids, values).values()]

    def required(self, ratio_id: str) -> list[str]:
        _ont, _norm, eng = _ensure()
        return eng.required(ratio_id)

    def list_ratios(self, category: str | None = None) -> list[RatioMeta]:
        ont, _norm, _eng = _ensure()
        out: list[RatioMeta] = []
        for r in ont.ratios.values():
            if category and r.category != category:
                continue
            out.append(_ratio_meta(r))
        return out

    def list_accounts(self, statement: str | None = None) -> list[AccountMeta]:
        ont, _norm, _eng = _ensure()
        out: list[AccountMeta] = []
        for a in ont.accounts.values():
            if statement and statement not in a.statement:
                continue
            out.append(_account_meta(a))
        return out

    def account(self, account_id: str) -> AccountMeta | None:
        ont, _norm, _eng = _ensure()
        a = ont.account(account_id)
        return _account_meta(a) if a else None


def _to_out(r: RatioResult) -> RatioResultOut:
    return RatioResultOut(
        ratio_id=r.ratio_id,
        value=r.value,
        ok=r.ok,
        missing=list(r.missing),
        warnings=list(r.warnings),
        reason=r.reason,
    )


def _ratio_meta(r: Ratio) -> RatioMeta:
    return RatioMeta(
        id=r.id,
        name=r.name,
        korean_name=r.korean_name,
        category=r.category,
        unit=r.unit,
        formula=r.formula,
        required_accounts=list(r.required_accounts),
    )


def _account_meta(a: Account) -> AccountMeta:
    return AccountMeta(
        id=a.id,
        korean_name=a.korean_name,
        english_name=a.english_name,
        statement=list(a.statement),
        category=list(a.category),
        parent=a.parent,
        children=list(a.children),
        ratios=list(a.ratios),
        aliases=list(a.aliases),
        sign=a.sign,
        formula=a.formula,
        description=a.description,
    )
