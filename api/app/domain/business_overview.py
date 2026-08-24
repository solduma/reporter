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
# 사업보고서가 없는 신규 상장사 폴백 소스(발행공시).
SECURITY = "security"  # 증권신고서
INVEST = "invest"  # 투자설명서

# 정기보고서 원문에서 추출하는 조악한 섹션 ID.
# - annual: 'II. 사업의 내용' 전체 블록(사업 개요·제품·시장위험·원재료·생산·판매·주주구성 포함).
# - half/quarter: 'I. 회사의 개황'(당해 기간 최근 경영사항) — annual 베이스 위에 덧붙일 갱신분.
SECTION_BUSINESS_CONTENT = "business_content"
SECTION_COMPANY_OVERVIEW = "company_overview"

# 투자자 관점 사업 개요 섹션 ID(조립 결과가 담을 구조).
# 산업 중립적 — 제조업/IT/금융/바이오 등 모든 산업에 공통 적용.
# "정보 없음"을 최소화: 해당 산업에 적용되지 않는 개념은 빈 값 대신 산업별 대체 정보를 표기.
INVESTOR_SECTIONS = (
    "company_profile",  # 회사 개요 — 법적지위/설립/상장/본점/신용등급/ESG
    "revenue_model",  # 수익 모델 — 매출 구성(제품/서비스/상품별), 성장률, 인식 기준
    "market_position",  # 시장 포지션 — 점유율/경쟁사/주요 고객/성장률
    "value_chain",  # 밸류체인·파트너십 — 공급자/고객/ 계열사/JV 관계
    "operating_drivers",  # 핵심 운영 드라이버 — 산업별 KPI(설비/R&D/AUM/파이프라인 등)
    "financial_highlights",  # 재무 하이라이트 — 매출/이익 추이(최근 3~5년)
    "ownership_governance",  # 지배구조·주주 — 최대주주/종속회사/배당/이사회
    "catalysts_and_risks",  # 향후 촉매·리스크 — 향후 이벤트/산업별 핵심 리스크
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
    """보고서 종류 → 추출할 조악 섹션 ID. annual·security·invest 는 사업 본문 계열,
    half/quarter 는 회사 개황."""
    if kind == ANNUAL:
        return SECTION_BUSINESS_CONTENT
    if kind in (SECURITY, INVEST):
        return SECTION_BUSINESS_CONTENT
    return SECTION_COMPANY_OVERVIEW
