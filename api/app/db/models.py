"""SQLAlchemy 2.0 ORM 모델. 1단계 범위: reports, report_analysis, daily_market_info."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
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


class Timeframe(enum.StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class PriceCandle(Base):
    """일/주/월봉 캐시. 네이버 신형 차트 API 응답을 upsert 한다."""

    __tablename__ = "price_candles"
    __table_args__ = (
        UniqueConstraint("stock_code", "timeframe", "bar_date", name="uq_candle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    timeframe: Mapped[Timeframe] = mapped_column(Enum(Timeframe))
    bar_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    foreign_ratio: Mapped[float | None] = mapped_column(Float)


class PriceCandleIntraday(Base):
    """30분봉(1분봉 리샘플 결과) 누적. 네이버 분봉 보존기간이 짧아 매 거래일 cron 으로 쌓는다."""

    __tablename__ = "price_candles_intraday"
    __table_args__ = (UniqueConstraint("stock_code", "bar_ts", name="uq_candle_intraday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    bar_ts: Mapped[datetime] = mapped_column(DateTime)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)


class Financial(Base):
    """기간별(연간/분기) 재무·밸류에이션. main.naver 스크래핑 결과 upsert."""

    __tablename__ = "financials"
    __table_args__ = (UniqueConstraint("stock_code", "period", name="uq_financial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))  # '2026.03' / '2026.12(E)'
    is_estimate: Mapped[bool] = mapped_column(default=False)
    revenue: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    bps: Mapped[float | None] = mapped_column(Float)
    per: Mapped[float | None] = mapped_column(Float)
    pbr: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Peer(Base):
    """동일업종비교 한 종목. 표시값을 JSON 유사 컬럼 대신 정규 컬럼으로 저장."""

    __tablename__ = "peers"
    __table_args__ = (
        UniqueConstraint("base_stock_code", "peer_stock_code", name="uq_peer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_stock_code: Mapped[str] = mapped_column(String(6), index=True)
    peer_stock_code: Mapped[str] = mapped_column(String(6))
    peer_name: Mapped[str] = mapped_column(String(128))
    price: Mapped[str | None] = mapped_column(String(32))
    market_cap: Mapped[str | None] = mapped_column(String(32))
    foreign_ratio: Mapped[str | None] = mapped_column(String(32))
    per: Mapped[str | None] = mapped_column(String(32))
    pbr: Mapped[str | None] = mapped_column(String(32))
    roe: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CorpCodeMap(Base):
    """DART corp_code ↔ 종목코드 매핑. corpCode.xml 을 주기적으로 적재한다."""

    __tablename__ = "corp_code_map"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    corp_code: Mapped[str] = mapped_column(String(8), index=True)
    corp_name: Mapped[str] = mapped_column(String(128))


class DisclosureSyncState(Base):
    """종목별 DART 마지막 동기화 시각. 공시가 0건이거나 신규가 없어도 재조회를 억제한다."""

    __tablename__ = "disclosure_sync_state"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Disclosure(Base):
    """DART 공시 1건 + 주가 긍/부정 센티먼트."""

    __tablename__ = "disclosures"
    __table_args__ = (UniqueConstraint("rcept_no", name="uq_disclosure_rcept"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    corp_code: Mapped[str] = mapped_column(String(8))
    rcept_no: Mapped[str] = mapped_column(String(14))
    report_nm: Mapped[str] = mapped_column(Text)
    flr_nm: Mapped[str] = mapped_column(String(128), default="")
    rcept_dt: Mapped[date] = mapped_column(Date, index=True)
    dart_url: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[Sentiment] = mapped_column(Enum(Sentiment), default=Sentiment.HOLD)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TradeStat(Base):
    """HS 품목별 월별 수출입 실적(전체 국가 합산). 관세청 API 캐시."""

    __tablename__ = "trade_stats"
    __table_args__ = (UniqueConstraint("hs_code", "period", name="uq_trade_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hs_code: Mapped[str] = mapped_column(String(12), index=True)
    period: Mapped[str] = mapped_column(String(7))  # 'YYYY.MM'
    export_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    import_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    balance_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UniverseSnapshot(Base):
    """전종목 일일 스냅샷 — 네이버 marketValue. 스몰캡 스크리너의 유니버스."""

    __tablename__ = "universe_snapshot"
    __table_args__ = (UniqueConstraint("snapshot_date", "stock_code", name="uq_universe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    market: Mapped[str] = mapped_column(String(8))  # KOSPI | KOSDAQ
    stock_name: Mapped[str] = mapped_column(String(128))
    stock_type: Mapped[str] = mapped_column(String(16), default="stock")  # stock | etf | etn ...
    close_price: Mapped[int | None] = mapped_column(BigInteger)
    change_pct: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, index=True)
    trading_value: Mapped[int | None] = mapped_column(BigInteger)
    three_month_rate: Mapped[float | None] = mapped_column(Float)


class DailyMarketInfo(Base):
    __tablename__ = "daily_market_info"
    __table_args__ = (UniqueConstraint("market_date", name="uq_market_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    source_count: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
