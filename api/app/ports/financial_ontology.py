"""OntologyPort — 재무 온톨로지 정규화·비율 계산 인터페이스.

DART taxonomy/한국 계정명을 단일 ontology ID 로 정규화하고, 정규화된 계정값으로 재무비율을
평가하는 기능을 이 포트에 의존시켜 구현(adapters/financial_ontology — financial_ontology 패키지)을
감춘다. LLM 포트와 동일한 경계 패턴: 서비스·라우터는 이 포트에 의존하고, 패키지 직접 import 는
어댑터만(import-linter ontology-behind-port 계약으로 강제).

DTO(NormalizeResult/RatioResultOut/RatioMeta/AccountMeta)는 순수 dataclass 로 외부 패키지
(financial_ontology)를 import 하지 않는다 — 어댑터가 패키지 객체를 이 DTO 로 변환한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class NormalizeResult:
    """계정 정규화 결과. id 는 정준 ontology ID(미해결 시 None)."""

    term: str
    id: str | None
    matched_via: str  # "id" | "taxonomy" | "korean_name" | "english_name" | "alias" | ""

    @property
    def resolved(self) -> bool:
        return self.id is not None


@dataclass(frozen=True)
class RatioResultOut:
    """비율 평가 결과. value None 시 reason 로 사유를 알린다(잘못된 값 반환 안 함)."""

    ratio_id: str
    value: Decimal | None
    ok: bool
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def display_value(self) -> str | None:
        return str(self.value) if self.value is not None else None


@dataclass(frozen=True)
class RatioMeta:
    """비율 메타데이터(목록 노출용)."""

    id: str
    name: str
    korean_name: str
    category: str
    unit: str | None
    formula: str
    required_accounts: list[str]
    description: str | None


@dataclass(frozen=True)
class AccountMeta:
    """계정 메타데이터(조회 노출용)."""

    id: str
    korean_name: str
    english_name: str
    statement: list[str]
    category: list[str]
    parent: str | None
    children: list[str]
    ratios: list[str]
    aliases: list[str]
    sign: str | None
    formula: str | None
    description: str | None


class OntologyPort(Protocol):
    """재무 온톨로지 정규화·비율 계산 포트."""

    def resolve(self, term: str, standard: str | None = None) -> NormalizeResult:
        """단일 term(계정명·taxonomy 요소·ID) → ontology ID 정규화."""
        ...

    def resolve_many(self, terms: list[str], standard: str | None = None) -> list[NormalizeResult]:
        """다수 term 일괄 정규화. 공시 항목→온톨로지 매핑 품질 점검용."""
        ...

    def calculate(self, ratio_id: str, values: dict[str, object]) -> RatioResultOut:
        """단일 비율 평가. values 는 계정 ID(평균/closing) + 명시 기간(id:opening) + 외부 입력."""
        ...

    def calculate_many(
        self, ratio_ids: list[str], values: dict[str, object]
    ) -> list[RatioResultOut]:
        """다수 비율 일괄 평가."""
        ...

    def required(self, ratio_id: str) -> list[str]:
        """해 비율의 필수 계정 ID. 공시 데이터 충족 여부 사전 점검용."""
        ...

    def list_ratios(self, category: str | None = None) -> list[RatioMeta]:
        """비율 정의 목록(카테고리 필터可选)."""
        ...

    def list_accounts(self, statement: str | None = None) -> list[AccountMeta]:
        """계정 목록(명세서 필터可选)."""
        ...

    def account(self, account_id: str) -> AccountMeta | None:
        """단일 계정 메타데이터 조회."""
        ...
