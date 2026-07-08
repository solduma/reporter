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
