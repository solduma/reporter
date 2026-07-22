"""계정명·taxonomy 요소를 온톨로지 ID로 정규화.

입력(이기준·이언어 공시의 한국 계정명, DART/IFRS/US GAAP XBRL taxonomy 요소, 영문명, 별칭)을
단일 ontology ID로 변환한다. RAG/LLM 분석·재무 정규화의 입력 단계.

해석 우선순위(명시적 standard 없을 때):
  1. 입력이 이미 ontology ID → 그대로
  2. standard 지정 시 해당 표준 taxonomy 매핑 우선
  3. 미지정 시 dart → korean_name → english_name → alias 순(한국 공시 1차)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Ontology

# 매핑 파일에 쓰인 DART XBRL taxonomy 접두어. 사용자 입력에 접두어 없이 들어와도 보정.
_DART_PREFIX = "ifrs-full_"
_WS = re.compile(r"\s+")


def _norm(term: str) -> str:
    """비교 정규화: 소문자화 + 공백 축소. 한국어는 그대로(대소문자 구분 없는 영문 매칭용)."""
    return _WS.sub(" ", term).strip()


@dataclass(frozen=True)
class Resolution:
    """정규화 결과. term은 원본 입력, id는 정준 ontology ID(미해결 시 None)."""

    term: str
    id: str | None
    matched_via: str  # "id" | "taxonomy" | "korean_name" | "english_name" | "alias" | ""

    @property
    def resolved(self) -> bool:
        return self.id is not None


class Normalizer:
    """온톨로지 기반 계정 정규화기."""

    def __init__(self, ontology: Ontology):
        self._ont = ontology

    def resolve(self, term: str, standard: str | None = None) -> str | None:
        """단일 term → ontology ID. 미해결 시 None."""
        return self.resolve_detail(term, standard=standard).id

    def resolve_detail(self, term: str, standard: str | None = None) -> Resolution:
        if not term:
            return Resolution(term=term, id=None, matched_via="")
        key = _norm(term)

        # 1) 이미 ontology ID
        if key in self._ont.accounts:
            return Resolution(term, key, "id")

        # 2) standard 지정 시 해당 표준 taxonomy 우선
        if standard:
            tid = self._lookup_taxonomy(key, standard)
            if tid:
                return Resolution(term, tid, "taxonomy")

        # 3) 표준 미지정 시 dart taxonomy 보조(DART XBRL 요소 입력 호환)
        tid = self._lookup_taxonomy(key, "dart")
        if tid:
            return Resolution(term, tid, "taxonomy")

        # 4) 한국 정준명
        tid = self._ont.by_korean_name.get(term) or self._ont.by_korean_name.get(key)
        if tid:
            return Resolution(term, tid, "korean_name")

        # 5) 영문명
        tid = self._ont.by_english_name.get(term) or self._ont.by_english_name.get(key)
        if tid:
            return Resolution(term, tid, "english_name")

        # 6) 별칭(한국·영문 혼재)
        tid = self._ont.by_alias.get(term) or self._ont.by_alias.get(key)
        if tid:
            return Resolution(term, tid, "alias")

        return Resolution(term, None, "")

    def resolve_many(self, terms: list[str], standard: str | None = None) -> list[Resolution]:
        return [self.resolve_detail(t, standard=standard) for t in terms]

    def coverage(self, terms: list[str], standard: str | None = None) -> float:
        """정규화 성공 비율(0~1). 공시 항목→온톨로지 매핑 품질 점검용."""
        if not terms:
            return 1.0
        ok = sum(1 for r in self.resolve_many(terms, standard=standard) if r.resolved)
        return ok / len(terms)

    def _lookup_taxonomy(self, key: str, standard: str) -> str | None:
        idx = self._ont.by_taxonomy.get(standard, {})
        if key in idx:
            return idx[key]
        # DART taxonomy 접두어 보정: "CashAndCashEquivalents" -> "ifrs-full_CashAndCashEquivalents"
        if standard == "dart" and not key.startswith(_DART_PREFIX):
            return idx.get(f"{_DART_PREFIX}{key}")
        return None
