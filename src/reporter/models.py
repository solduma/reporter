"""도메인 데이터 구조."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    """네이버 금융 리서치 목록의 리포트 한 건."""

    category: str
    title: str
    broker: str
    date: str  # YY.MM.DD
    views: int
    read_url: str | None = None
    pdf_url: str | None = None
    stock_name: str | None = None
    stock_code: str | None = None
    industry: str | None = None  # 산업분석 목록의 '분류' 컬럼(업종명)

    # 파이프라인 진행 중 채워지는 값
    score: float = 0.0
    text: str = ""  # PDF 추출 텍스트
    summary: str = ""  # 1차 요약

    @property
    def label(self) -> str:
        who = self.stock_name or self.broker
        return f"[{self.category}] {self.title} ({who})"


CATEGORY_NAMES = {
    "company": "종목분석",
    "industry": "산업분석",
    "market_info": "시황정보",
    "invest": "투자정보",
    "economy": "경제분석",
    "debenture": "채권분석",
}


# 카테고리를 batch 단위로 묶어 시간차 실행 (증권사 리포트가 9~11시 순차 발행)
BATCHES: dict[int, list[str]] = {
    1: ["company", "industry"],
    2: ["market_info", "invest"],
    3: ["economy"],
    4: ["debenture"],
}


@dataclass
class Briefing:
    """AI 종합 분석 결과."""

    text: str
    report_count: int
    categories: list[str] = field(default_factory=list)


@dataclass
class DigestResult:
    """카테고리 장문 종합 + 인용도 상위 소스 선정 결과."""

    text: str
    category: str
    report_count: int
    sources: list[Report] = field(default_factory=list)  # 인용 상위(발췌 많이 된) 리포트
