"""비즈니스 온톨로지 도메인 모델 — 기업/산업/제품/원재료/부문 노드와 가치사슬 엣지.

financial_ontology.models 와 동일한 frozen-dataclass + from_dict 패턴을 따른다.
노드는 YAML SOT(ontology/*.yaml)로 정의되고, 엣지는 인스턴스 데이터(LLM 추출 + normalizer)로
구성되므로 모델은 노드 타입과 엣지 타입을 모두 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeType = Literal["company", "industry", "product", "raw_material", "segment"]
SegmentType = Literal["industry", "product", "region", "sales_channel"]

# 엣지 타입 — schema에 열거된 전체 목록. v1에서 실제로 생성하는 엣지는 일부지만,
# 타입 집합은 전체를 정의해 둔다(v2 확장시 스키마 변경 최소화).
EdgeType = Literal[
    "operates_in",
    "manufactures",
    "uses_material",
    "supplies",
    "supplies_to",
    "competes_with",
    "parent_of",
    "subsidiary_of",
    "has_segment",
    "has_facility",
    "exports_to",
    "led_by",
    "owns_ip",
    "substitute_of",
    "part_of_value_chain",
]

# Porter 가치사슬 단계 — part_of_value_chain 엣지의 chain_stage 속성값.
ChainStage = Literal[
    "inbound",
    "operations",
    "outbound",
    "marketing",
    "service",
    "firm_infrastructure",
    "hr",
    "technology_development",
    "procurement",
]

# 원재료 상품 분류 — 향후 가격 연동(LME 금속/CAS 화학/BP·IEA 에너지)을 위한 참조.
CommodityTaxonomy = Literal["LME", "CAS", "BP", "IEA", "other"]


@dataclass(frozen=True)
class IndustryNode:
    """GICS 기반 산업 노드. 4단계 rollup(sector/group/industry/sub-industry)을 모두 보관."""

    id: str
    gics_code: str  # 8자리 sub-industry 코드 (예: 45203020)
    gics_sector: str  # 2자리
    gics_group: str  # 4자리
    gics_industry: str  # 6자리
    gics_sub_industry: str  # 8자리
    korean_name: str
    english_name: str
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict) -> IndustryNode:
        return cls(
            id=str(raw["id"]),
            gics_code=str(raw["gics_code"]),
            gics_sector=str(raw["gics_sector"]),
            gics_group=str(raw["gics_group"]),
            gics_industry=str(raw["gics_industry"]),
            gics_sub_industry=str(raw["gics_sub_industry"]),
            korean_name=str(raw["korean_name"]),
            english_name=str(raw["english_name"]),
            aliases=tuple(raw.get("aliases") or ()),
        )


@dataclass(frozen=True)
class ProductNode:
    """제품 노드. 제품 겸 원재료(실리콘 웨이퍼 등)는 is_also_material_id 로 교차링크."""

    id: str
    korean_name: str
    english_name: str
    aliases: tuple[str, ...] = ()
    commodity_type: str | None = None
    is_also_material_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> ProductNode:
        return cls(
            id=str(raw["id"]),
            korean_name=str(raw["korean_name"]),
            english_name=str(raw.get("english_name", "")),
            aliases=tuple(raw.get("aliases") or ()),
            commodity_type=raw.get("commodity_type"),
            is_also_material_id=raw.get("is_also_material_id"),
        )


@dataclass(frozen=True)
class RawMaterialNode:
    """원재료 노드. commodity_ref 로 상품 분류 체계(LME/CAS/BP/IEA)에 연결."""

    id: str
    korean_name: str
    english_name: str
    aliases: tuple[str, ...] = ()
    commodity_ref: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> RawMaterialNode:
        return cls(
            id=str(raw["id"]),
            korean_name=str(raw["korean_name"]),
            english_name=str(raw.get("english_name", "")),
            aliases=tuple(raw.get("aliases") or ()),
            commodity_ref=dict(raw.get("commodity_ref") or {}),
        )


@dataclass(frozen=True)
class CompanyNode:
    """기업 노드. 정준 키는 DART corp_code(안정적). stock_code 는 거래 식별자.

    companies.yaml 은 시드(주요 상장사)만 담고, 대부분의 기업 노드는 인스턴스 데이터로
    CorpCodeMap 역해석을 통해 생성된다.
    """

    id: str
    corp_code: str
    stock_code: str | None
    korean_name: str
    english_name: str
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict) -> CompanyNode:
        return cls(
            id=str(raw["id"]),
            corp_code=str(raw["corp_code"]),
            stock_code=raw.get("stock_code"),
            korean_name=str(raw["korean_name"]),
            english_name=str(raw.get("english_name", "")),
            aliases=tuple(raw.get("aliases") or ()),
        )


@dataclass(frozen=True)
class SegmentNode:
    """부문 노드 — iotHom3MdQe 부문별 매출(산업/제품/지역/매출형태)."""

    id: str
    segment_type: SegmentType
    korean_name: str
    english_name: str
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict) -> SegmentNode:
        return cls(
            id=str(raw["id"]),
            segment_type=str(raw["segment_type"]),  # type: ignore[arg-type]
            korean_name=str(raw["korean_name"]),
            english_name=str(raw.get("english_name", "")),
            aliases=tuple(raw.get("aliases") or ()),
        )


@dataclass(frozen=True)
class EdgeTypeMeta:
    """엣지 타입 메타 — schema 열거 + 방향성·설명. 그래프 구성시 참조."""

    id: str
    korean_name: str
    english_name: str
    directed: bool = True
    description: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> EdgeTypeMeta:
        return cls(
            id=str(raw["id"]),
            korean_name=str(raw["korean_name"]),
            english_name=str(raw.get("english_name", "")),
            directed=bool(raw.get("directed", True)),
            description=raw.get("description"),
        )


@dataclass(frozen=True)
class Edge:
    """엣지 인스턴스 — LLM 추출 + normalizer 가 생성. source_quote 는 원문 verbatim(감사증적).

    엣지는 YAML SOT가 아니라 인스턴스 데이터이므로 from_dict 만 제공한다(영속측에서 사용).
    """

    src: str  # 노드 canonical_id
    dst: str  # 노드 canonical_id
    edge_type: EdgeType
    share: float | None = None
    period: str | None = None  # 예: "2024.12"
    source_quote: str | None = None
    source_rcept: str | None = None
    source_section_id: str | None = None
    chain_stage: ChainStage | None = None
    confidence: float | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Edge:
        return cls(
            src=str(raw["src"]),
            dst=str(raw["dst"]),
            edge_type=str(raw["edge_type"]),  # type: ignore[arg-type]
            share=raw.get("share"),
            period=raw.get("period"),
            source_quote=raw.get("source_quote"),
            source_rcept=raw.get("source_rcept"),
            source_section_id=raw.get("source_section_id"),
            chain_stage=raw.get("chain_stage"),
            confidence=raw.get("confidence"),
        )


@dataclass
class BusinessOntology:
    """로드된 온톨로지 전체 — 노드 사전들 + 역색인 + 메타데이터.

    financial_ontology.Ontology 와 동일하게 역색인(by_korean_name/by_english_name/by_alias)을
    loader 가 구성한다. 엣지는 인스턴스 데이터이므로 온톨로지 객체에 포함되지 않는다.
    """

    industries: dict[str, IndustryNode]
    products: dict[str, ProductNode]
    materials: dict[str, RawMaterialNode]
    companies: dict[str, CompanyNode]
    segments: dict[str, SegmentNode]
    edge_types: dict[str, EdgeTypeMeta]
    metadata: dict
    # 역색인 — normalizer 가 사용. 값은 canonical_id.
    by_korean_name: dict[str, str] = field(default_factory=dict)
    by_english_name: dict[str, str] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)
    # 산업 전용 — GICS 코드/alias 로 sub-industry 노드를 찾는다.
    by_gics_code: dict[str, str] = field(default_factory=dict)
    # DART/KRX 산업코드 → GICS sub-industry canonical_id (mappings/*.yaml 로부터).
    by_industry_code: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def node_ids(self) -> set[str]:
        ids: set[str] = set()
        for d in (self.industries, self.products, self.materials, self.companies, self.segments):
            ids.update(d.keys())
        return ids

    def industry(self, node_id: str) -> IndustryNode | None:
        return self.industries.get(node_id)

    def product(self, node_id: str) -> ProductNode | None:
        return self.products.get(node_id)

    def material(self, node_id: str) -> RawMaterialNode | None:
        return self.materials.get(node_id)

    def company(self, node_id: str) -> CompanyNode | None:
        return self.companies.get(node_id)

    def segment(self, node_id: str) -> SegmentNode | None:
        return self.segments.get(node_id)
