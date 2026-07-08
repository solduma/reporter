"""API 응답 스키마 (Pydantic)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class MarketBrief(BaseModel):
    market_date: date | None
    summary: str


class ReportCard(BaseModel):
    id: int
    category: str
    title: str
    broker: str
    name: str | None  # 종목명 또는 산업명
    summary: str
    sentiment: str  # BUY | SELL | HOLD
    rationale: str
    published_date: date
    has_pdf: bool


class IndustrySummary(BaseModel):
    industry: str
    report_count: int


class ReportRef(BaseModel):
    id: int
    title: str
    broker: str
    sentiment: str
    summary: str
    read_url: str | None
    has_pdf: bool


class SentimentPoint(BaseModel):
    date: date
    avg_sentiment: float  # BUY=+1 / HOLD=0 / SELL=-1 평균
    reports: list[ReportRef]


class TradePoint(BaseModel):
    period: str  # 'YYYY.MM'
    export_usd: int
    import_usd: int
    balance_usd: int


class CandlePoint(BaseModel):
    t: str  # ISO 시각 (일/주/월봉은 YYYY-MM-DD, 30분봉은 ISO datetime)
    o: float
    h: float
    low: float
    c: float
    v: int


class CompanySummary(BaseModel):
    stock_code: str
    stock_name: str | None


class FinancialPeriodOut(BaseModel):
    period: str
    is_estimate: bool
    revenue: float | None
    operating_income: float | None
    net_income: float | None
    eps: float | None
    per: float | None
    pbr: float | None
    roe: float | None


class PeerOut(BaseModel):
    stock_code: str
    name: str
    price: str | None
    market_cap: str | None
    foreign_ratio: str | None
    per: str | None
    pbr: str | None
    roe: str | None


class TimelineItem(BaseModel):
    type: str  # 'report' | 'disclosure'
    date: date
    title: str
    source: str  # 증권사(리포트) 또는 제출인(공시)
    sentiment: str  # BUY | SELL | HOLD
    rationale: str
    link: str | None
    report_id: int | None = None  # 리포트면 PDF 조회용 id
