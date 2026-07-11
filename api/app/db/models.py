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
from sqlalchemy.dialects.postgresql import JSONB
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
    # 국내 6자리 코드·지수(KOSPI)뿐 아니라 미국 심볼(QQQ.O·XLK 등)도 저장하므로 16자.
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
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
    # DART 재무제표 크롤 산출(EV/EBITDA·PSR). ebitda·net_debt 은 원 단위 원자료.
    ebitda: Mapped[float | None] = mapped_column(Float)
    net_debt: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    psr: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReportFinancial(Base):
    """DART 정규 보고서(사업/반기/분기) 원문 XML 파싱 결과 — 기간·연결구분별 원본 재무.

    financials(네이버+파생 밸류)와 분리해 '보고서에서 직접 읽은 값'을 원본 그대로 보존한다.
    감가상각/무형상각은 현금흐름표 D&A 를 정밀 파싱(구조화 API 엔 대형사 D&A 가 없다).
    금액은 전부 원 단위로 정규화(원문은 원/천원/백만원 혼재)해 저장한다.
    """

    __tablename__ = "report_financials"
    __table_args__ = (
        UniqueConstraint("stock_code", "period", "fs_div", name="uq_report_financial"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))  # '2023.12'(사업)·'2023.06'(반기)·'2024.03'(분기)
    fs_div: Mapped[str] = mapped_column(String(3))  # CFS(연결) | OFS(별도)
    report_kind: Mapped[str] = mapped_column(String(8))  # annual | half | quarter
    rcept_no: Mapped[str] = mapped_column(String(14))  # 출처 접수번호
    # 원 단위 원본값(파싱 실패 항목은 None). 기간값(매출·손익·상각)은 보고서 기간 그대로.
    revenue: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)  # 지배주주
    equity: Mapped[float | None] = mapped_column(Float)  # 지배주주 자본(BS 시점)
    eps: Mapped[float | None] = mapped_column(Float)
    # 현금흐름표 D&A. 파서가 감가상각+무형자산상각을 합산해 depreciation 에 담는다(개별 분리
    # 불가한 종목이 많아). amortization 은 예비 컬럼(현재 미사용, 항상 None).
    depreciation: Mapped[float | None] = mapped_column(Float)  # 감가상각비+무형자산상각비 합
    amortization: Mapped[float | None] = mapped_column(Float)  # 예비(미사용)
    parsed_at: Mapped[datetime] = mapped_column(
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
    ev_ebitda: Mapped[str | None] = mapped_column(String(32))  # 동일업종 비교(#139)
    psr: Mapped[str | None] = mapped_column(String(32))
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


class ValuationSyncState(Base):
    """종목별 EV/EBITDA·PSR 마지막 산출 시각. 분기 단위라 24h TTL 로 DART 재조회 억제."""

    __tablename__ = "valuation_sync_state"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketQuote(Base):
    """지수·환율 실시간 시세 시계열. 대시보드가 매 요청 네이버를 타지 않고, 스냅샷을 DB 에
    쌓아 최신값 조회 + 시계열 보존. name(코스피·원/달러·나스닥 등)+ts 로 유니크."""

    __tablename__ = "market_quote"
    __table_args__ = (UniqueConstraint("name", "ts", name="uq_market_quote"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="")  # 'us'|'kr'|'fx'
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    close: Mapped[str] = mapped_column(String(32), default="")  # 표시 문자열 보존
    change: Mapped[str] = mapped_column(String(32), default="")
    change_ratio: Mapped[str] = mapped_column(String(32), default="")
    rising: Mapped[bool | None] = mapped_column()


class SyncState(Base):
    """범용 종목별 동기화 시각. (domain, stock_code) 키로 재무·peers 등 외부 스크랩의 TTL 을
    관리해, 조회 때마다 네이버를 타지 않고 DB 우선 + 만료 시 백그라운드 갱신하게 한다."""

    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("domain", "stock_code", name="uq_sync_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(24), index=True)  # 'financials' | 'peers' | ...
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
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
    three_month_rate: Mapped[float | None] = mapped_column(Float)  # 네이버 제공(대개 결측)
    momentum_3m: Mapped[float | None] = mapped_column(Float)  # price_candles 로 계산한 3개월 수익률%


class GrowthMetric(Base):
    """종목 성장지표 — financials 분기 데이터에서 파생한 YoY·흑자전환 캐시."""

    __tablename__ = "growth_metric"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_growth_stock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))  # 기준 분기
    revenue_yoy: Mapped[float | None] = mapped_column(Float)
    op_yoy: Mapped[float | None] = mapped_column(Float)
    op_turnaround: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DailyMarketInfo(Base):
    __tablename__ = "daily_market_info"
    __table_args__ = (UniqueConstraint("market_date", name="uq_market_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    source_count: Mapped[int] = mapped_column(BigInteger, default=0)
    # 시황 생성 국면: forecast(개장 전 예상)/intraday(장중 실시간)/closing(마감 리뷰).
    # 웹 카드에서 국면 배지로 노출한다.
    phase: Mapped[str] = mapped_column(String(16), default="")
    # 같은 영업일 행을 국면 전환마다 덮어쓰므로, 마지막 갱신 시각을 별도로 보존해
    # "장중 · HH:MM 기준"을 표시한다(created_at 은 최초 생성 시각으로 고정).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisComment(Base):
    """종목 분석 LLM 종합 코멘트 캐시.

    코멘트 생성은 Ollama 호출로 ~17초 걸려 매 요청 동기 생성하면 화면이 느리다. 축 점수·지표
    입력의 해시(inputs_hash)로 캐시해, 입력이 같으면 저장분을 즉시 주고, 없거나 바뀌면
    백그라운드로 재생성한다. stock_code 당 1행(최신 입력 기준)만 유지한다.
    """

    __tablename__ = "analysis_comment"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_analysis_comment_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    inputs_hash: Mapped[str] = mapped_column(String(16))  # 축 점수·지표 입력 해시(캐시 유효성)
    comment: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FallbackEvent(Base):
    """폴백(1차 소스/방법 실패 → 2차 대안 전환) 발생 이력.

    reporter.fallback.log_fallback 이 계측 지점에서 1행씩 남긴다. TUI 폴백 패널이
    최근 이력과 key 별 집계를 보여준다. 운영 관측성 전용이라 다른 테이블과 조인하지 않는다.
    """

    __tablename__ = "fallback_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)  # 계층 식별자 예: "chart.naver_to_kis"
    reason: Mapped[str] = mapped_column(Text, default="")  # 무엇이 실패했는지(사람이 읽는 요약)
    detail: Mapped[str] = mapped_column(Text, default="")  # 대상 식별자(종목코드·URL 등) 옵션
    context: Mapped[dict] = mapped_column(JSONB, default=dict)  # 추가 구조화 맥락


class IngestLog(Base):
    """크롤링·적재 배치 실행 이력(append-only). 실행 1회당 1행.

    스케줄러 5개 잡과 TUI 수동 트리거가 종료 시 결과를 남긴다. sync_state 는 (domain,code)당
    최신 시각만 upsert 라 이력이 안 남으므로, '언제 무엇을 얼마나 적재했는지'는 여기서 본다.
    운영 관측성 전용(다른 테이블과 조인하지 않음).
    """

    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    job: Mapped[str] = mapped_column(String(32), index=True)  # 예: "ingest_cycle" | "candle_batch"
    status: Mapped[str] = mapped_column(String(8), default="ok")  # ok | fail
    rows: Mapped[int] = mapped_column(Integer, default=0)  # 수집·적재 건수(잡별 대표 수치)
    detail: Mapped[str] = mapped_column(Text, default="")  # 결과 요약(사람이 읽는 한 줄)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)  # 실행 소요(ms)


class BroadcastKind(enum.StrEnum):
    """텔레그램 발송 콘텐츠 유형. digest_* 는 카테고리 장문 종합, 나머지는 뉴스/리서치."""

    DIGEST_MARKET = "digest_market"
    DIGEST_INVEST = "digest_invest"
    DIGEST_ECON = "digest_econ"
    DIGEST_BOND = "digest_bond"
    CLOSING = "closing"
    MARKET_NEWS = "market_news"
    PREMARKET = "premarket"
    AFTERNOON = "afternoon"
    MORNING = "morning"
    PER_ENTITY = "per_entity"


class Broadcast(Base):
    """텔레그램으로 발송된 메시지 아카이브.

    CLI 파이프라인이 발송 직후 스풀(logs/broadcasts.jsonl)에 남기고, API 가 이를 읽어
    멱등 적재한다(Postgres 단일 writer 는 API). source_refs/stock_codes/industries 로
    기존 리포트·공시·수출 이력과 조인해 산업별·종목별 흐름 타임라인에 합류시킨다.
    """

    __tablename__ = "broadcast"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_broadcast_dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[BroadcastKind] = mapped_column(Enum(BroadcastKind), index=True)
    ref_date: Mapped[date] = mapped_column(Date, index=True)  # 콘텐츠 대상 영업일
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 발송 시각
    title: Mapped[str] = mapped_column(Text, default="")  # 메시지 헤더 (예: "📈 시황 종합")
    body: Mapped[str] = mapped_column(Text, default="")  # 발송 원문(분할 전 전체)
    # {reports:[{broker,title,url}], news:[{title,url,source}], keywords:[...]}
    source_refs: Mapped[dict] = mapped_column(JSONB, default=dict)
    stock_codes: Mapped[list] = mapped_column(JSONB, default=list)  # 언급 종목코드(종목 흐름 조인)
    industries: Mapped[list] = mapped_column(JSONB, default=list)  # 언급 산업(산업 흐름 조인)
    dedup_key: Mapped[str] = mapped_column(String(128))  # "{kind}|{ref_date}|{seq}" 재실행 멱등
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SectorTheme(Base):
    """judal.co.kr 테마(섹터). 수급 섹터 로테이션의 섹터 정의."""

    __tablename__ = "sector_theme"
    __table_args__ = (UniqueConstraint("judal_idx", name="uq_sector_theme_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judal_idx: Mapped[int] = mapped_column(Integer, index=True)  # judal themeIdx
    name: Mapped[str] = mapped_column(String(64))
    stock_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SectorThemeStock(Base):
    """테마↔종목 매핑. 한 종목이 여러 테마에 속할 수 있다."""

    __tablename__ = "sector_theme_stock"
    __table_args__ = (UniqueConstraint("judal_idx", "stock_code", name="uq_theme_stock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judal_idx: Mapped[int] = mapped_column(Integer, index=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
