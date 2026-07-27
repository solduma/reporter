"""BusinessOntologyPort — 기업/산업/제품/원재료 온톨로지 정규화·조회 인터페이스.

사업 개요 원문의 자유텍스트 NER 결과(raw mention)를 정준 노드 ID 로 해석하고, 정적 온톨로지
(GICS 산업 트리·제품/원재료/부문 사전·엣지 타입 메타)를 노출하는 기능을 이 포트에 의존시켜
구현(adapters/business_ontology — business_ontology 패키지)을 감춘다. OntologyPort(재무)와
동일한 경계 패턴: 서비스·라우터는 이 포트에 의존하고, 패키지 직접 import 는 어댑터만
(import-linter business-ontology-behind-port 계약으로 강제).

DTO(BusinessNormalizeResult/IndustryNodeOut/NodeOut/EdgeTypeOut)는 순수 dataclass 로 외부 패키지
(business_ontology)를 import 하지 않는다 — 어댑터가 패키지 객체를 이 DTO 로 변환한다.
엣지 인스턴스(그래프)는 DB 영속 데이터이므로 이 포트가 아니라 서비스가 DB 에서 읽는다(§4 하이브리드).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

# 포트 리프 — 패키지의 NodeType 리터럴을 재정의하지 않고 동일 문자열 집합을 alias 로 둔다.
BusinessNodeType = Literal["company", "industry", "product", "raw_material", "segment"]
BusinessResolveStatus = Literal["canonical", "pending_review", "unknown"]


@dataclass(frozen=True)
class BusinessNormalizeResult:
    """raw mention 의 정준화 결과. canonical_id None 시 미해결(pending_review/unknown)."""

    term: str
    node_type: BusinessNodeType | None
    canonical_id: str | None
    matched_via: (
        str  # "id"|"korean_name"|"english_name"|"alias"|"gics_code"|"industry_code"|"fuzzy"|""
    )
    status: BusinessResolveStatus
    confidence: float

    @property
    def resolved(self) -> bool:
        return self.canonical_id is not None and self.status == "canonical"


@dataclass(frozen=True)
class IndustryNodeOut:
    """GICS 산업 노드 — 4단계 rollup(sector/group/industry/sub-industry) 메타."""

    id: str
    gics_code: str
    gics_sector: str
    gics_group: str
    gics_industry: str
    gics_sub_industry: str
    korean_name: str
    english_name: str
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeOut:
    """정적 노드(제품/원재료/기업/부문) 메타 — 타입별 공통 필드. 산업은 IndustryNodeOut 사용."""

    id: str
    node_type: BusinessNodeType
    korean_name: str
    english_name: str
    aliases: list[str] = field(default_factory=list)
    # 타입별 확장(해당 시에만 채움).
    commodity_type: str | None = None  # product
    is_also_material_id: str | None = None  # product 겸 원재료 교차링크
    commodity_ref: dict[str, str] = field(default_factory=dict)  # raw_material
    corp_code: str | None = None  # company
    stock_code: str | None = None  # company
    segment_type: str | None = None  # segment


@dataclass(frozen=True)
class EdgeTypeOut:
    """엣지 타입 메타 — schema 열거 + 방향성·설명. 그래프 구성시 참조."""

    id: str
    korean_name: str
    english_name: str
    directed: bool = True
    description: str | None = None


class BusinessOntologyPort(Protocol):
    """비즈니스 온톨로지 정규화·정적 조회 포트(엣지 인스턴스는 DB → 서비스가 담당)."""

    def resolve(
        self, raw: str, node_type: BusinessNodeType, standard: str | None = None
    ) -> BusinessNormalizeResult:
        """단일 raw mention → 정준 노드 ID 해석. company/industry/product/raw_material/segment."""
        ...

    def resolve_many(
        self,
        mentions: list[tuple[str, BusinessNodeType]],
        standard: str | None = None,
    ) -> list[BusinessNormalizeResult]:
        """다수 mention 일괄 해석. NER 추출 결과→정준 ID 매핑 품질 점검용."""
        ...

    def list_industries(self) -> list[IndustryNodeOut]:
        """GICS 산업 풀(11/24/54/128 4단계 rollup) 평면 목록."""
        ...

    def industry(self, gics_code_or_id: str) -> IndustryNodeOut | None:
        """GICS 코드(8자리) 또는 노드 ID 로 단일 산업 조회."""
        ...

    def list_edge_types(self) -> list[EdgeTypeOut]:
        """엣지 타입 메타 목록(15종)."""
        ...

    def list_nodes(self, node_type: BusinessNodeType | None = None) -> list[NodeOut]:
        """정적 노드 목록(시드 products/materials/companies/segments). 타입 필터可选."""
        ...

    def node(self, node_id: str) -> NodeOut | None:
        """단일 정적 노드 조회(ID). 산업 노드도 NodeOut 로 변환해 반환."""
        ...
