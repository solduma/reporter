"""사업 개요 도메인 — 정기보고서에서 뽑은 사업 내용을 투자자 관점으로 정리한 구조.

재무 온톨로지(계정·비율)와 무관한 별도 도메인. 공시(DART) → DB(BusinessReportRaw /
BusinessOverviewCache) → Cache API 흐름의 순수 데이터 구조를 정의한다. 어댑터·서비스가
이 구조를 생산·소비한다.

베이스는 가장 최근 사업보고서(annual)의 '사업의 내용' 본문. 이후 발행된 반기·분기보고서의
'회사의 개황'(최근 경영사항)을 오버레이해 업데이트분을 반영한다. LLM 이 표 중심 투자자
관점으로 정리정돈한다(원문 그대로 아님).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 정기보고서 종류 — report_ingest._target_reports 와 동일 표현.
ANNUAL = "annual"
HALF = "half"
QUARTER = "quarter"
PERIODIC_KINDS = (ANNUAL, HALF, QUARTER)

# 정기보고서 원문에서 추출하는 조악한 섹션 ID.
# - annual: 'II. 사업의 내용' 전체 블록(사업 개요·제품·시장위험·원재료·생산·판매·주주구성 포함).
# - half/quarter: 'I. 회사의 개황'(당해 기간 최근 경영사항) — annual 베이스 위에 덧붙일 갱신분.
SECTION_BUSINESS_CONTENT = "business_content"
SECTION_COMPANY_OVERVIEW = "company_overview"

# 사업보고서 '사업의 내용' 내 투자자 관점 하위 섹션 ID(조립 결과가 담을 섹션 구조).
INVESTOR_SECTIONS = (
    "business_summary",  # 사업 개요 — 무엇을 파는가, 사업 영역, 종속사
    "main_products",  # 주요 제품·서비스 — 제품군/매출비중/전망 표
    "market_risk",  # 시장·가격 위험 — 위험요인/영향/대응 표
    "raw_materials",  # 원재료 — 원재료/조달처/비중 표
    "production",  # 생산·설비 — 사업장/설비/가동 표
    "sales",  # 판매 — 고객/매출비중/조건 표
    "ownership",  # 주주구성·최대주주 — 주주/지분/관계 표
    "recent_updates",  # 최근 경영사항(반기·분기 반영) — 갱신 표
)


@dataclass
class BusinessTable:
    """투자자 관점 정리 표. headers → rows 2차원. 표 제목은 선택."""

    title: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class BusinessSection:
    """조립된 사업 개요의 한 섹션 — 서술(마크다운) + 표."""

    id: str
    title: str
    narrative: str = ""
    tables: list[BusinessTable] = field(default_factory=list)
    # 이 섹션을 마지막으로 갱신한 정기보고서(반기/분기 오버레이 추적). None = annual 베이스.
    updated_by_rcept: str | None = None
    updated_by_kind: str | None = None


@dataclass
class SourceReport:
    """조립에 사용된 정기보고서 출처."""

    rcept_no: str
    kind: str  # annual | half | quarter
    period: str  # '2024.12' | '2025.03' ...
    is_base: bool = False  # 베이스 사업보고서 여부


@dataclass
class BusinessOverview:
    """종목 사업 개요 캐시 페이로드 — 공시 → DB → Cache 응답의 정준 구조."""

    stock_code: str
    stock_name: str = ""
    as_of_annual_rcept: str = ""  # 베이스 사업보고서 접수번호
    source_reports: list[SourceReport] = field(default_factory=list)
    sections: list[BusinessSection] = field(default_factory=list)
    # 리서치+ 결과 요약(Phase 3 에서 추가). None = 리서치 미실행.
    research_summary: dict | None = None


def section_id_for_kind(kind: str) -> str:
    """정기보고서 종류 → 추출할 조악 섹션 ID. annual 은 사업의 내용, half/quarter 는 회사 개황."""
    if kind == ANNUAL:
        return SECTION_BUSINESS_CONTENT
    return SECTION_COMPANY_OVERVIEW
