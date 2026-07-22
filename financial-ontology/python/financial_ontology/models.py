"""온톨로지 계정·비율 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Standard = Literal["dart", "ifrs", "usgaap"]


@dataclass(frozen=True)
class Account:
    """정규화된 단일 계정 노드. 온톨로지의 단일 진실원 표현."""

    id: str
    name: str
    korean_name: str
    english_name: str
    statement: tuple[str, ...]
    category: tuple[str, ...]
    sign: str | None
    parent: str | None
    children: tuple[str, ...]
    depends_on: tuple[str, ...]
    affects: tuple[str, ...]
    ratios: tuple[str, ...]
    cashflow_mapping: tuple[str, ...]
    aliases: tuple[str, ...]
    mappings: dict[str, tuple[str, ...]]
    formula: str | None
    description: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> Account:
        m = raw.get("mappings") or {}
        return cls(
            id=raw["id"],
            name=raw.get("name", raw.get("korean_name", raw["id"])),
            korean_name=raw.get("korean_name", ""),
            english_name=raw.get("english_name", ""),
            statement=tuple(raw.get("statement", [])),
            category=tuple(raw.get("category", [])),
            sign=raw.get("sign"),
            parent=raw.get("parent"),
            children=tuple(raw.get("children", [])),
            depends_on=tuple(raw.get("depends_on", [])),
            affects=tuple(raw.get("affects", [])),
            ratios=tuple(raw.get("ratios", [])),
            cashflow_mapping=tuple(raw.get("cashflow_mapping", [])),
            aliases=tuple(raw.get("aliases", [])),
            mappings={k: tuple(v) for k, v in m.items()},
            formula=raw.get("formula"),
            description=raw.get("description"),
        )

    @property
    def is_contra(self) -> bool:
        """차감 계정 여부(자기주식·대손충당금 등)."""
        return self.sign == "negative" or "contra" in self.category


@dataclass(frozen=True)
class Ratio:
    """재무비율 정의. formula는 사람 가독용 서술, engine이 평가에 사용한다."""

    id: str
    name: str
    korean_name: str
    formula: str
    required_accounts: tuple[str, ...]
    depends_on: tuple[str, ...]
    affects: tuple[str, ...]
    category: str
    unit: str | None
    higher_is_better: str | None
    description: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> Ratio:
        return cls(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            korean_name=raw.get("korean_name", ""),
            formula=raw.get("formula", ""),
            required_accounts=tuple(raw.get("required_accounts", [])),
            depends_on=tuple(raw.get("depends_on", [])),
            affects=tuple(raw.get("affects", [])),
            category=raw.get("category", ""),
            unit=raw.get("unit"),
            higher_is_better=raw.get("higher_is_better"),
            description=raw.get("description"),
        )


@dataclass
class Ontology:
    """로드된 온톨로지 전체. 계정·비율·명세서·정규화 인덱스를 보유."""

    accounts: dict[str, Account]
    ratios: dict[str, Ratio]
    statements: dict[str, dict]
    metadata: dict
    # standard -> {taxonomy_or_name -> account_id}
    by_taxonomy: dict[str, dict[str, str]] = field(default_factory=dict)
    by_korean_name: dict[str, str] = field(default_factory=dict)
    by_english_name: dict[str, str] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)

    @property
    def account_ids(self) -> set[str]:
        return set(self.accounts)

    def account(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    def ratio(self, ratio_id: str) -> Ratio | None:
        return self.ratios.get(ratio_id)
