"""사업 리서치 결과 도메인. Phase 3a에서는 dict로 직렬화해 BusinessOverviewCache.payload["research_summary"]에 저장.
Phase 3b에서 온톨로지 노드/엣지로 승격(ResearchEntity→Node, ValueChainLink→Edge).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResearchEntity:
    """공급자/고객/경쟁사 엔티티. Phase 3b에서 BusinessOntologyNode로."""

    name: str
    role: str  # 원재료 공급자, 주요 고객, 경쟁사 등
    note: str = ""  # 추가 세부사항(선택)


@dataclass
class ValueChainLink:
    """밸류체인 연결. Phase 3b에서 BusinessOntologyEdge로."""

    stage: str  # 원료 조달, 생산, 유통, 서비스 등
    direction: str  # upstream, downstream
    entity: str  # 관련 회사/법인/산업명
    note: str = ""


@dataclass
class OntologyMention:
    """사업보고서 원문에서 LLM 이 추출한 비즈니스 온톨로지 언급(대상 노드 + 회사와의 관계).

    LLM/normalizer 분리: LLM 은 raw name + source_quote(원문 verbatim) 만 내고, 결정론적
    normalizer 가 정준 canonical_id 를 부여. edge_type 은 회사(주체) → 대상 노드 관계.
    company/industry/product/raw_material/segment. 정준화·ID 부여는 하지 않는다.
    """

    node_type: str  # 대상 노드 타입: company/industry/product/raw_material/segment
    name: str  # 대상 raw name(정준화 전)
    edge_type: str  # manufactures/uses_material/operates_in/competes_with/supplies_to/supplies/has_segment/...
    share: float | None = None  # 0~1 비중(있을 때만)
    period: str | None = None  # 'YYYY.MM' 또는 연도
    source_quote: str = ""  # 원문 verbatim 발췌(감사증적, 필수)
    confidence: float = 0.0  # LLM 자체 신뢰도(정규화 confidence 와 별도)


@dataclass
class ResearchSummary:
    """리서치+ 결과 요약. LLM이 산출하는 구조화 결과 + 서술."""

    guideline: str  # 사용자 가이드라인 입력
    vendors: list[ResearchEntity]  # 주요 원재료/공급자
    customers: list[ResearchEntity]  # 주요 고객
    competitors: list[ResearchEntity]  # 경쟁사
    value_chain: list[ValueChainLink]  # 밸류체인 단계별 관계
    narrative_md: str  # 종합 서술 마크다운
    generated_at: str  # ISO 8601 timestamp
    model: str  # 사용한 LLM 모델


# LLM이 산출해야 할 JSON 스키마 상수(프롬프트에 주입).
_RESEARCH_SCHEMA = """
{
  "vendors": [{"name": "회사명", "role": "공급자 역할(예: 주요 원재료 공급, 부품 조달)", "note": "비고(선택)"}],
  "customers": [{"name": "회사명", "role": "고객 역할(예: 최종 제품 구매자, 납품처)", "note": "비고(선택)"}],
  "competitors": [{"name": "회사명", "role": "경쟁 양상(예: 동일 세그먼트 경쟁, 대체재 위협)", "note": "비고(선택)"}],
  "value_chain": [{"stage": "밸류체인 단계(원료 조달/생산/유통/서비스 등)", "direction": "upstream 또는 downstream", "entity": "관계 대상", "note": "비고(선택)"}],
  "narrative_md": "사업 리서치 종합 서술(마크다운, 500자 내외). 공급망·고객사·경쟁환경·밸류체인 위치를 요약. 출처(사업개요·공시·웹)를 인용."
}

각 항목은 최소 0개(없으면 빈 배열). narrative는 필수. 회사명은 국내 상장법인/해외 법인/산업군 모두 가능.
"""
