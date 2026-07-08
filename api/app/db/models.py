"""SQLAlchemy 2.0 ORM 모델. 1단계 범위: reports, report_analysis, daily_market_info."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Sentiment(enum.StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("read_url", name="uq_reports_read_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    broker: Mapped[str] = mapped_column(String(64))
    published_date: Mapped[date] = mapped_column(Date, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)

    stock_code: Mapped[str | None] = mapped_column(String(6), index=True)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    industry_name: Mapped[str | None] = mapped_column(String(128), index=True)

    read_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    pdf_object_key: Mapped[str | None] = mapped_column(Text)  # MinIO 객체 키
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis: Mapped[ReportAnalysis | None] = relationship(
        back_populates="report", uselist=False, cascade="all, delete-orphan"
    )


class ReportAnalysis(Base):
    __tablename__ = "report_analysis"

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[Sentiment] = mapped_column(Enum(Sentiment), default=Sentiment.HOLD)
    rationale: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report: Mapped[Report] = relationship(back_populates="analysis")


class DailyMarketInfo(Base):
    __tablename__ = "daily_market_info"
    __table_args__ = (UniqueConstraint("market_date", name="uq_market_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    source_count: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
