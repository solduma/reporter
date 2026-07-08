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
