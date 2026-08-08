"""SQLAlchemy 2.0 ORM 모델. 1단계 범위: reports, report_analysis, daily_market_info."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
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
    # PDF 원문 발췌(앞 N쪽). 요약(summary·rationale)엔 대표주만 남아 산업 리포트의 개별 종목 언급을
    # 놓치므로, 종목명 검색(tool_reports)이 원문을 뒤지도록 저장한다. 분량 상한으로 보관.
    full_text: Mapped[str | None] = mapped_column(Text)
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
    __table_args__ = (UniqueConstraint("stock_code", "timeframe", "bar_date", name="uq_candle"),)

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
    __table_args__ = (UniqueConstraint("stock_code", "period", "fs_div", name="uq_financial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))  # '2026.03' / '2026.12(E)'
    fs_div: Mapped[str] = mapped_column(String(3), default="CFS")  # CFS(연결) | OFS(별도)
    is_estimate: Mapped[bool] = mapped_column(default=False)
    revenue: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    bps: Mapped[float | None] = mapped_column(Float)
    per: Mapped[float | None] = mapped_column(Float)
    pbr: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    dps: Mapped[float | None] = mapped_column(Float)  # 주당배당금(원)
    div_yield: Mapped[float | None] = mapped_column(Float)  # 시가배당률(배당수익률, %)
    # ev_ebitda 는 report_ingest(원문 XML 정밀 D&A·역사 시총), psr 은 financials_backfill 소유.
    # ebitda·net_debt(원 단위 원자료)은 구 valuation_ingest 잔재로 현재 미사용(과거 행에만 값 존재).
    ebitda: Mapped[float | None] = mapped_column(Float)
    net_debt: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    psr: Mapped[float | None] = mapped_column(Float)
    depreciation: Mapped[float | None] = mapped_column(Float)  # D&A(억원) — FCFF 산출용
    capex: Mapped[float | None] = mapped_column(Float)  # 자본적지출(억원) — FCFF 산출용
    effective_tax_rate: Mapped[float | None] = mapped_column(Float)  # 실효세율(소수) — WACC·NOPAT
    cost_of_debt: Mapped[float | None] = mapped_column(Float)  # 부채비용(소수, 이자/총차입) — WACC
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
    period: Mapped[str] = mapped_column(
        String(16)
    )  # '2023.12'(사업)·'2023.06'(반기)·'2024.03'(분기)
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
    capex: Mapped[float | None] = mapped_column(
        Float
    )  # 자본적지출(유형+무형 취득, CF) — FCFF 산출용
    income_tax: Mapped[float | None] = mapped_column(Float)  # 법인세비용 — 실효세율 분자
    pretax_income: Mapped[float | None] = mapped_column(Float)  # 세전이익 — 실효세율 분모
    interest_expense: Mapped[float | None] = mapped_column(Float)  # 이자비용 — 부채비용 분자
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FinancialStatement(Base):
    """DART fnlttSinglAcntAll 전체 재무제표 라인아이템 — JSONB 원본 보존.

    DART API 응답의 모든 account_id 항목을 sj_div(BS/IS/CIS/CF)로 그룹화해 JSONB로 저장한다.
    financials(가공 밸류)와 분리해 원본 데이터를 그대로 보존한다.
    백필 시 DART 응답과 함께 저장되며, 온디맨드 조회 시에도 채워진다.
    """

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("stock_code", "period", "fs_div", name="uq_financial_statement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))  # '2026.03'
    fs_div: Mapped[str] = mapped_column(String(3))  # CFS(연결) | OFS(별도)
    # JSONB: {"BS": [...], "IS": [...], "CIS": [...], "CF": [...]}
    # 각 항목: {account_id, account_nm, amount, sj_div, level}
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FsParseGap(Base):
    """FinancialStatement 원문(JSONB) → IncomeEquity 파싱 실패 이력.

    파이프라인 원칙: FS 원문 먼저 파싱 → 실패 필드는 DART 폴백으로 채우되, 어떤
    필드/매핑(account_id) 이 빠졌는지 기록해 온톨로지 매핑 보완 워크플로우를 제공한다.
    (stock_code, period, fs_div, field) 유일 — 같은 갭은 갱신만(중복 적재 방지).
    """

    __tablename__ = "fs_parse_gaps"
    __table_args__ = (
        UniqueConstraint("stock_code", "period", "fs_div", "field", name="uq_fs_parse_gap"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    period: Mapped[str] = mapped_column(String(16))
    fs_div: Mapped[str] = mapped_column(String(3))
    field: Mapped[str] = mapped_column(String(32))  # revenue|operating_income|capex|...
    # 기대한 온톨로지 매핑 account_id 집합(파싱 시도한). 매핑 보완의 단서.
    expected_aids: Mapped[str] = mapped_column(Text, default="")
    # FS 원문에 실제 있던 account_id 중 매칭된 것(빈 문자열=아예 매칭 없음).
    found_aids: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(64), default="")  # no_match|amount_none|no_fs
    fallback: Mapped[str] = mapped_column(String(16), default="")  # dart|skip — 폴백 처리 결과
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Peer(Base):
    """동일업종비교 한 종목. 표시값을 JSON 유사 컬럼 대신 정규 컬럼으로 저장."""

    __tablename__ = "peers"
    __table_args__ = (UniqueConstraint("base_stock_code", "peer_stock_code", name="uq_peer"),)

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
    # DART 표준산업분류코드(corpCode.xml induty_code). 무료 산업분류 → GICS 2차 anchor.
    # 신규 컬럼(_COLUMN_MIGRATIONS 로 추가). 과거 적재분은 NULL.
    induty_code: Mapped[str | None] = mapped_column(String(16), nullable=True)


class SegmentSales(Base):
    """부문별 매출(iotHom3MdQe) — 사업보고서 원문의 구조화 DART 소스.

    제품/지역/산업/매출형태 부문의 매출액·비중을 종목·사업연도·보고서 단위로 저장.
    비즈니스 온톨로지 has_segment 엣지 + /api/business-ontology/{code}/segments 응답 원천.
    """

    __tablename__ = "segment_sales"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    bsns_year: Mapped[str] = mapped_column(String(4))
    report_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    segment_type: Mapped[str] = mapped_column(String(16))  # 산업/제품/지역/매출형태 구분
    segment_name: Mapped[str] = mapped_column(String(128))
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratio_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "stock_code",
            "bsns_year",
            "report_code",
            "segment_type",
            "segment_name",
            name="uq_segment_sales",
        ),
    )


class BusinessOntologyNode(Base):
    """비즈니스 온톨로지 노드 인스턴스 — 회사별로 추출된 기업/산업/제품/원재료/부문 노드.

    정적 온톨로지 패키지의 정준 canonical_id 를 회사 컨텍스트로 인스턴스화. LLM 추출 원문 언급을
    결정론적 normalizer 가 해석 — 정준 미해결(pending_review)도 저장하되 status 로 구분(자동 병합 금지,
    수동 리뷰 후 canonical 승격). (stock_code, node_type, korean_name) = 회사 내 원문 언급 단위.
    """

    __tablename__ = "business_ontology_node"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    node_type: Mapped[str] = mapped_column(
        String(16)
    )  # company/industry/product/raw_material/segment
    canonical_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    korean_name: Mapped[str] = mapped_column(String(128))
    english_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="canonical")  # canonical/pending_review
    source_rcept: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("stock_code", "node_type", "korean_name", name="uq_bont_node"),
    )


class BusinessOntologyEdge(Base):
    """비즈니스 온톨로지 엣지 인스턴스 — 회사 노드와 인접 노드 간 관계(manufactures/uses_material/...).

    src 는 회사(주체) 노드, dst 는 대상 노드. source_quote 는 원문 verbatim 발췌(감사증적).
    (stock_code, src_node_id, dst_node_id, edge_type, period) 로 회사 내 엣지 중복 제거.
    """

    __tablename__ = "business_ontology_edge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    src_node_id: Mapped[int] = mapped_column(ForeignKey("business_ontology_node.id"), index=True)
    dst_node_id: Mapped[int] = mapped_column(ForeignKey("business_ontology_node.id"), index=True)
    edge_type: Mapped[str] = mapped_column(String(32))
    share: Mapped[float | None] = mapped_column(Float, nullable=True)
    period: Mapped[str] = mapped_column(String(16), default="")
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_rcept: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chain_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "src_node_id", "dst_node_id", "edge_type", "period", name="uq_bont_edge"
        ),
    )


class DisclosureSyncState(Base):
    """종목별 DART 마지막 동기화 시각·깊이. 공시가 0건이거나 신규가 없어도 재조회를 억제한다.

    synced_from 은 이 종목을 어디까지(과거 하한) 동기화했는지 — TTL 이 유효해도 요청 창이 그보다
    더 과거를 원하면(begin < synced_from) 재조회해야 하므로 깊이를 함께 추적한다. 얕은 정기배치
    (최근 14일)가 stamp 한 뒤 온디맨드 2년 조회가 TTL 로 스킵돼 과거를 못 채우는 것을 막는다.
    """

    __tablename__ = "disclosure_sync_state"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    synced_from: Mapped[date | None] = mapped_column(Date)  # 동기화가 도달한 과거 하한(없으면 미상)


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


class RelatedCompany(Base):
    """종목별 관계사(모/자회사·출자사) — DART 최대주주·타법인출자에서 수집.

    웹서치 관련성 판정 alias 원천: 제목엔 종목명이 없어도 본문에 모/자회사가 언급된 기사를
    관련으로 포착하기 위함. related_name 을 CorpCodeMap 로 역해석하면 상장 관계사(related_stock_code)
    를 링크한다. 종목·관계사명·관계 유니크로 멱등 upsert.
    """

    __tablename__ = "related_company"
    __table_args__ = (
        UniqueConstraint("stock_code", "related_name", "relation", name="uq_related_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)  # 기준 종목
    related_name: Mapped[str] = mapped_column(String(128))  # 관계사명(alias 원천)
    relation: Mapped[str] = mapped_column(String(12))  # parent | subsidiary | investor
    stake_pct: Mapped[float | None] = mapped_column(Float)  # 지분율(%)
    related_stock_code: Mapped[str | None] = mapped_column(String(6))  # 관계사가 상장사면 링크
    source: Mapped[str] = mapped_column(String(24), default="")  # hyslrSttus | otrCprInvstmntSttus
    bsns_year: Mapped[int | None] = mapped_column(Integer)  # 근거 사업연도
    # otrCprInvstmntSttus 추가 필드 — 자회사 필터(이익 10%+/적자/출자목적)용.
    inv_purpose: Mapped[str | None] = mapped_column(String(64))  # 출자목적(자회사/관계회사/신사업…)
    book_value: Mapped[int | None] = mapped_column(BigInteger)  # 기말 장부가액(원)
    sub_total_assets: Mapped[int | None] = mapped_column(BigInteger)  # 자회사 총자산(원)
    sub_net_profit: Mapped[int | None] = mapped_column(BigInteger)  # 자회사 당기순이익(원)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Shareholder(Base):
    """종목별 주주 명부 — DART 최대주주현황(hyslrSttus)의 개별 행.

    RelatedCompany 가 모/자회사 '법인' 관계(웹서치 alias 원천)에만 쓰이므로, 주주 개인·특수관계인
    행 단위 지분(이름/관계/지분율)은 별도 테이블로 저장한다. 기업분석 화면 지분구조 좌측 표 원천.
    related_stock_code 로 상장 주주(법인)를 내부 종목 링크한다.
    """

    __tablename__ = "shareholder"
    __table_args__ = (UniqueConstraint("stock_code", "holder_name", name="uq_shareholder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)  # 기준 종목
    holder_name: Mapped[str] = mapped_column(String(128))  # 주주명(법인/개인)
    relate: Mapped[str] = mapped_column(
        String(64), default=""
    )  # 최대주주 본인/배우자/자녀/특수관계인
    stake_pct: Mapped[float | None] = mapped_column(Float)  # 기말 지분율(%)
    is_corporate: Mapped[bool] = mapped_column(Boolean, default=False)  # 법인 여부
    related_stock_code: Mapped[str | None] = mapped_column(String(6))  # 상장 주주면 링크
    bsns_year: Mapped[int | None] = mapped_column(Integer)  # 근거 사업연도
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OwnershipChangeCache(Base):
    """임원·주요주주 소유변동(elestock) 캐시 — 엔드포인트 12h 캐싱.

    elestock 은 시계열 변동 이력이라 본 테이블에 매입(영속)하지 않고 최신 스냅샷만 캐시한다.
    기업분석 화면 '최근 지분 변동' 표 원천. DART 호출 빈도 제한(종목당 12h 1회).
    """

    __tablename__ = "ownership_change_cache"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)  # [{rcept_no, date, reporter, ...}]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OwnershipSummary(Base):
    """종목별 지분구조 요약 — 합산 지분율·유통주식 등 분석 배지 원천.

    야간 related_company_ingest.backfill_stock 에서 hyslrSttus(합산) + stockTotqySttus(유통)를
    계산해 영속. 기업분석 화면 지분구조 섹션 상단 분석 배지(지배력·수급) 원천.
    """

    __tablename__ = "ownership_summary"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    group_stake_pct: Mapped[float | None] = mapped_column(Float)  # 최대주주+특수관계인 합산(%)
    floating_shares: Mapped[int | None] = mapped_column(BigInteger)  # 유통주식수
    floating_ratio: Mapped[float | None] = mapped_column(Float)  # 유통비율(%)
    bsns_year: Mapped[int | None] = mapped_column(Integer)  # 근거 사업연도
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MajorHolderCache(Base):
    """5%+ 대량보유주주(majorstock.json) 캐시 — 엔드포인트 12h 캐싱.

    OwnershipChangeCache 와 동일 패턴. DART 호출 빈도 제한(종목당 12h 1회).
    """

    __tablename__ = "major_holder_cache"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)  # [{rcept_dt, repror, stkrt, ...}]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DilutionCache(Base):
    """CB/BW 발행내역(cvbdIsDecsn·bdwtIsDecsn) 캐시 — 엔드포인트 12h 캐싱.

    전환사채·신주인수권부사채 발행결정을 캐시해 잠재 지분 희석률 계산. 동일 패턴.
    """

    __tablename__ = "dilution_cache"

    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)  # [{type, bd_fta, cv_prc, cvisstk_cnt, ...}]
    updated_at: Mapped[datetime] = mapped_column(
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
    momentum_3m: Mapped[float | None] = mapped_column(
        Float
    )  # price_candles 로 계산한 3개월 수익률%
    rs_rating: Mapped[int | None] = mapped_column(SmallInteger)  # IBD RS Rating 1~99(전종목 백분위)
    trend_score: Mapped[float | None] = mapped_column(
        Float
    )  # 기술적 추세 종합 0~100(야간 배치, 종목분석과 동일)


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
    op_status: Mapped[str | None] = mapped_column(String(8))  # 흑자전환|흑자지속|적자전환|적자지속
    # 영업이익률 변화(당기-직전동기, 비율). 영업이익 성장 축(상태+마진 pp) + 흑전 규모.
    op_margin_delta: Mapped[float | None] = mapped_column(Float)
    eps_yoy: Mapped[float | None] = mapped_column(Float)  # 주당순이익 YoY(스냅샷 표시·PEG 산출)
    # 순이익·EBITDA 도 영업이익과 동일하게 손익상태 4단계 + 마진 증감 pp 로 성장 축을 만든다.
    net_status: Mapped[str | None] = mapped_column(String(8))
    net_margin_delta: Mapped[float | None] = mapped_column(Float)
    ebitda_status: Mapped[str | None] = mapped_column(String(8))
    ebitda_margin_delta: Mapped[float | None] = mapped_column(Float)
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


class RiskFreeRate(Base):
    """국고채 등 무위험수익률 일별 캐시(ECOS 배치). 밸류에이션이 최신 행을 읽어 상수 대신 사용."""

    __tablename__ = "risk_free_rate"
    __table_args__ = (UniqueConstraint("maturity", "rate_date", name="uq_risk_free_rate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    maturity: Mapped[str] = mapped_column(String(32), index=True)  # kr_treasury_3y 등
    rate_date: Mapped[date] = mapped_column(Date, index=True)
    rate: Mapped[float] = mapped_column(Float)  # 연 % (예 3.24)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketPremium(Base):
    """시장 위험프리미엄(ERP) 캐시(Damodaran 배치). 밸류에이션이 최신 행을 읽어 상수 대신 사용."""

    __tablename__ = "market_premium"
    __table_args__ = (UniqueConstraint("source", "as_of_date", name="uq_market_premium"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # damodaran_kr_erp 등
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    erp: Mapped[float] = mapped_column(Float)  # 소수(0.0487 = 4.87%)
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


class TrendCache(Base):
    """종목 기술적 추세(/trend) 사전계산 캐시.

    compute_trend 는 3프레임 와인스타인 국면·스윙구조·박스·상대강도(rs_series 2000+포인트)를
    매 요청 재계산해 warm 1초+ 걸린다. 입력(일봉·지수)은 하루 1회 배치로만 갱신되므로, 야간
    candle_batch 가 CompanyTrend 응답 JSON 을 미리 만들어 저장하고 엔드포인트는 읽기만 한다.
    rs_rating 은 장중 갱신되는 스칼라라 페이로드에 넣지 않고 조회 시 스냅샷에서 붙인다.
    stock_code 당 1행(최신)만 유지한다(AnalysisComment 패턴).
    """

    __tablename__ = "trend_cache"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_trend_cache_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    as_of: Mapped[date] = mapped_column(Date)  # 계산 기준 최신 확정봉 날짜(신선도 판정)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # CompanyTrend 응답(rs_rating 제외)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TimelineCache(Base):
    """종목 타임라인 응답 캐시.

    GET /companies/{code}/timeline 은 매 요청 3개 테이블(reports+report_analysis, disclosures,
    broadcasts)을 조인·정렬한다. 이 응답을 stock_code 당 1행 JSONB 로 캐시해 조회를 즉시 반환하고,
    POST /timeline/refresh 가 DART 신규 공시 동기화 후 캐시를 재구축한다.
    last_disclosure_date 는 다음 refresh 시 DART 조회 시작일로 사용한다.
    """

    __tablename__ = "timeline_cache"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_timeline_cache_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # list[TimelineItem] as JSON
    last_disclosure_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FinancialStatementCache(Base):
    """재무제표 응답 캐시.

    GET /companies/{code}/financial-statements 는 매 요청 모든 기간의 JSONB 를 파싱해
    Python 에서 계층 구조를 재조립(280줄)한다. 이 조립 결과를 (stock_code, fs_div) 당 1행
    JSONB 로 캐시해 cache hit 시 조립을 생략하고 즉시 반환한다. 백필(FinancialStatement
    upsert) 시 invalidate_financial_statements_cache 가 해당 종목 행을 지운다.
    """

    __tablename__ = "financial_statement_cache"
    __table_args__ = (UniqueConstraint("stock_code", "fs_div", name="uq_fs_cache_code_div"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    fs_div: Mapped[str] = mapped_column(String(3))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # FinancialStatementsOut 직렬화
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FallbackEvent(Base):
    """폴백(1차 소스/방법 실패 → 2차 대안 전환) 발생 이력.

    reporter.fallback.log_fallback 이 계측 지점에서 1행씩 남긴다. TUI 폴백 패널이
    최근 이력과 key 별 집계를 보여준다. 운영 관측성 전용이라 다른 테이블과 조인하지 않는다.
    """

    __tablename__ = "fallback_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
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
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    job: Mapped[str] = mapped_column(String(32), index=True)  # 예: "ingest_cycle" | "candle_batch"
    status: Mapped[str] = mapped_column(String(8), default="ok")  # ok | fail
    rows: Mapped[int] = mapped_column(Integer, default=0)  # 수집·적재 건수(잡별 대표 수치)
    detail: Mapped[str] = mapped_column(Text, default="")  # 결과 요약(사람이 읽는 한 줄)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)  # 실행 소요(ms)


class NewsArticle(Base):
    """매크로/테마 뉴스 원문 — Google News RSS 수집분. LLM 이벤트 분류의 입력·이력 보존.

    link 로 멱등 dedup. 분류 결과(이벤트 유형·테마·관련 종목)는 StockEvent 로 전파한다.
    """

    __tablename__ = "news_article"
    __table_args__ = (UniqueConstraint("link", name="uq_news_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(128), default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # LLM 분류 결과: event_kind(신기술|공급망|규제|매크로|실적 등), 관련 테마·요약.
    event_kind: Mapped[str] = mapped_column(String(16), default="")
    theme: Mapped[str] = mapped_column(String(64), default="")  # 매칭된 sector_theme 명(있으면)
    summary: Mapped[str] = mapped_column(Text, default="")  # LLM 한 줄 요약
    classified: Mapped[bool] = mapped_column(default=False)  # LLM 분류 완료 여부
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockEvent(Base):
    """종목별 뉴스 이벤트 — 뉴스 분류 결과를 테마 매핑으로 구성종목에 전파한 것.

    이벤트드리븐 스크리너가 공시·리포트·급등락과 함께 이 테이블(뉴스 이벤트)을 조회한다.
    (stock_code, news_id) 로 멱등. 한 뉴스가 여러 구성종목에 퍼진다.
    """

    __tablename__ = "stock_event"
    __table_args__ = (UniqueConstraint("stock_code", "news_id", name="uq_stock_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_article.id"), index=True)
    event_kind: Mapped[str] = mapped_column(
        String(16), default=""
    )  # 신기술|공급망|규제|매크로|실적
    theme: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    event_date: Mapped[date] = mapped_column(Date, index=True)  # 뉴스 발행일
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class UsFinancial(Base):
    """US 종목 재무 스냅샷 — SEC EDGAR companyfacts 산출 지표(종목당 1행, TTL 갱신).

    KR 재무(financials, String(6))와 별개 테이블로 US 를 격리한다(기존 KR 스키마 무변경).
    ticker 는 대문자 심볼(예 NVDA·AAPL). 값은 USD·표시 지표.
    """

    __tablename__ = "us_financials"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    ttm_revenue: Mapped[float | None] = mapped_column(Float)  # USD
    ttm_net_income: Mapped[float | None] = mapped_column(Float)
    ttm_operating_income: Mapped[float | None] = mapped_column(Float)
    ttm_eps: Mapped[float | None] = mapped_column(Float)
    equity: Mapped[float | None] = mapped_column(Float)
    shares: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)  # 근사(분기말 종가 x 주식수), USD
    per: Mapped[float | None] = mapped_column(Float)
    pbr: Mapped[float | None] = mapped_column(Float)
    psr: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)  # %
    # raw_ontology 는 읽기 전용 스냅샷이고 SQLite 단위 테스트도 지원해야 하므로 JSON (portable) 사용.
    raw_ontology: Mapped[list[dict] | None] = mapped_column(
        JSON, default=None, doc="SEC companyfacts XBRL 계정 ontology 정규화 원시 데이터"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UsUniverse(Base):
    """US 유니버스 스냅샷 — 스크리너용(네이버 시세 야간 배치). ticker String(16), USD 지표.

    KR UniverseSnapshot(String(6), KRW)과 별개 테이블로 US 격리(Approach A). 스크리너 필터·
    정렬(시총·거래대금·PER/PBR·모멘텀)에 필요한 필드를 담는다.
    """

    __tablename__ = "us_universe"
    __table_args__ = (UniqueConstraint("snapshot_date", "ticker", name="uq_us_universe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    naver_symbol: Mapped[str] = mapped_column(String(24))  # .O/bare 해석된 차트 심볼
    name: Mapped[str] = mapped_column(String(128))
    exchange: Mapped[str | None] = mapped_column(String(16))  # NASDAQ | NYSE ...
    sector: Mapped[str | None] = mapped_column(String(64))  # GICS 섹터
    close_price: Mapped[float | None] = mapped_column(Float)  # USD
    change_pct: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float, index=True)  # USD
    trading_value: Mapped[float | None] = mapped_column(Float)  # 거래대금 USD
    per: Mapped[float | None] = mapped_column(Float)
    pbr: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)
    momentum_3m: Mapped[float | None] = mapped_column(Float)  # 3개월 수익률% (봉에서 계산)


class UsDisclosure(Base):
    """US 공시(SEC EDGAR 8-K 등) — KR Disclosure(DART, String(6)/rcept_no)와 별개 테이블.

    accession(18자+대시)·form_type·CIK 는 DART 스키마에 안 맞아 US 전용으로 둔다(Approach A).
    sentiment/rationale 는 조회 시 LLM 요약(비용 통제). filing_date·primary_doc_url 로 원문 링크.
    """

    __tablename__ = "us_disclosures"
    __table_args__ = (UniqueConstraint("accession", name="uq_us_disclosure"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    cik: Mapped[str] = mapped_column(String(10))
    accession: Mapped[str] = mapped_column(String(24))  # 0000320193-24-000123
    form_type: Mapped[str] = mapped_column(String(16))  # 8-K | 10-K | 10-Q ...
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    primary_doc_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)  # 8-K item 요약(수집 시 기재)
    sentiment: Mapped[Sentiment | None] = mapped_column(Enum(Sentiment))
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalendarEvent(Base):
    """경제/실적 캘린더 이벤트 — 매크로 지표 발표·주요기업 실적·중대일(FOMC·선거 등).

    forward event_date 를 가지는 유일한 테이블(다른 event 계열은 모두 과거 발생일). 지나간
    이벤트는 actual/previous + LLM impact_text(지수 영향·이유), 도래 전 이벤트는 consensus +
    LLM expectation_text(시장 기대치)를 담는다. LLM 텍스트는 inputs_hash 로 재생성 여부 판정
    (analysis_comment 캐싱 패턴). (source, source_key) 로 멱등 upsert.
    """

    __tablename__ = "calendar_event"
    __table_args__ = (UniqueConstraint("source", "source_key", name="uq_calendar_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)  # 발표·발생 예정/실제일
    region: Mapped[str] = mapped_column(String(8), default="US")  # US | KR | GLOBAL
    kind: Mapped[str] = mapped_column(
        String(16), default="macro"
    )  # macro|earnings|fomc|election|geo
    title: Mapped[str] = mapped_column(String(200))  # 예: "미국 CPI (6월)"
    importance: Mapped[int] = mapped_column(SmallInteger, default=2)  # 1(낮음)~3(높음)
    # 수치(있을 때만) — 문자열로 원표기 보존(단위·부호·% 다양).
    actual: Mapped[str | None] = mapped_column(String(32))  # 실제치(지난 이벤트)
    previous: Mapped[str | None] = mapped_column(String(32))  # 직전치
    consensus: Mapped[str | None] = mapped_column(String(32))  # 시장 예상치(있으면)
    unit: Mapped[str | None] = mapped_column(String(16))  # %, K, pt 등
    # LLM 생성 텍스트(해시 캐싱).
    impact_text: Mapped[str | None] = mapped_column(Text)  # 지난 이벤트: 지수 영향·이유
    # 지난 이벤트 지수 영향 방향(LLM 분류): positive|negative|neutral. 프론트 색칠용.
    impact_direction: Mapped[str | None] = mapped_column(String(8))
    expectation_text: Mapped[str | None] = mapped_column(Text)  # 미래 이벤트: 시장 기대치
    inputs_hash: Mapped[str | None] = mapped_column(String(64))  # LLM 입력 해시(재생성 판정)
    # 출처 추적(멱등 upsert 키).
    source: Mapped[str] = mapped_column(String(24), default="manual")  # fred|manual|nasdaq|...
    source_key: Mapped[str] = mapped_column(String(64))  # 출처 내 고유키(release_id+date 등)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeepDiveJob(Base):
    """종목 딥다이브 작업큐 — DB 폴링 큐(Redis/Celery 미도입). worker 가 pending 을 잡아 실행.

    한 종목당 진행 중(pending|running) job 은 최대 1건(라우터가 중복 enqueue 방지). status
    상태기계로 진행률·현재 단계를 추적하고 프론트가 폴링한다. hitl_* 는 2차 HITL 용 자리(1차 미사용).
    """

    __tablename__ = "deepdive_job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    status: Mapped[str] = mapped_column(
        String(12), default="pending", index=True
    )  # pending|running|paused|done|failed
    current_stage: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0~5(완료 단계)
    progress: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0~100
    model: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str | None] = mapped_column(Text)
    # 2차 HITL: 특정 단계 후 paused 로 멈추고 사용자 피드백을 받아 재개(1차는 미사용, 스키마만 확보).
    # HITL(밸류에이션 직전 사람 개입): thesis 후 paused 로 멈춰 hitl_prompt 로 인풋을 청하고,
    # 사용자가 hitl_input 을 넣으면 재개해 추가 리서치·검증 후 밸류에이션에 반영한다.
    hitl_pending: Mapped[bool] = mapped_column(default=False)  # 인풋 대기 중(프론트가 입력창 노출)
    hitl_prompt: Mapped[str | None] = mapped_column(Text)  # 사용자에게 보일 질문
    hitl_input: Mapped[str | None] = mapped_column(Text)  # 사용자가 제출한 인풋(있으면 재개·반영)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeepDiveReport(Base):
    """종목 딥다이브 결과 — 5단계 구조화 JSON + 통합 서술 본문. 종목당 최신 1건 유지(재실행 갱신).

    단계별 JSON 은 프론트가 섹션 카드로 렌더·개별 재생성에 쓰고, narrative_md 는 사람이 읽는 최종
    보고서. verdict/upside_pct 는 스크리너 정렬·필터용 요약. inputs_hash 로 재생성 판정.
    """

    __tablename__ = "deepdive_report"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_deepdive_stock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("deepdive_job.id"))
    model: Mapped[str] = mapped_column(String(64), default="")
    # 단계별 구조화 결과(JSONB). 미완 단계는 null.
    overview_json: Mapped[dict | None] = mapped_column(JSONB)
    redflags_json: Mapped[dict | None] = mapped_column(JSONB)
    business_json: Mapped[dict | None] = mapped_column(JSONB)
    thesis_json: Mapped[dict | None] = mapped_column(JSONB)
    # HITL(밸류에이션 직전 사용자 인풋) 검증 결과 — 인풋별 판정(반박/반영/가능성)과 근거. 밸류에이션에 주입.
    hitl_json: Mapped[dict | None] = mapped_column(JSONB)
    valuation_json: Mapped[dict | None] = mapped_column(JSONB)
    narrative_md: Mapped[str | None] = mapped_column(Text)  # 5단계 통합 서술 본문
    verdict: Mapped[str | None] = mapped_column(
        String(120)
    )  # 결론 요약(예: '성장주 · 업사이드 62%')
    upside_pct: Mapped[float | None] = mapped_column(Float)  # 목표가 업사이드(정렬·필터용)
    inputs_hash: Mapped[str | None] = mapped_column(String(64))  # 재생성 판정
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IrInterviewJob(Base):
    """주담(IR) 인터뷰 전략 생성 job — 딥다이브 완료 후 별도 호흡으로 실행하는 독립 큐.

    worker 가 pending 을 폴링해 에이전틱 파이프라인(전략 아이템 도출 → 아이템별 질문 fan-out,
    각 단계 reviewer 검증)을 실행한다. 딥다이브와 독립(HITL·단계 재개 없음)이라 상태가 단순하다.
    """

    __tablename__ = "ir_interview_job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    status: Mapped[str] = mapped_column(
        String(12), default="pending", index=True
    )  # pending|running|done|failed
    progress: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0~100
    model: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IrInterviewReport(Base):
    """주담 인터뷰 전략 결과 — 종목당 최신 1건. strategy_json 은 아이템→질문 트리(아래 구조).

    strategy_json = {"strategy_items": [{"item","why_matters","linked_valuation_assumption",
    "questions": [{"q","intent","valuation_link","expected_signal"}]}], "total_questions": N}.
    valuation 민감변수 기반이라 딥다이브 밸류에이션이 갱신되면 재생성 대상(as_of 로 신선도).
    """

    __tablename__ = "ir_interview_report"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_ir_interview_report_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    job_id: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64), default="")
    strategy_json: Mapped[dict | None] = mapped_column(JSONB)
    total_questions: Mapped[int] = mapped_column(SmallInteger, default=0)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeepDiveShare(Base):
    """딥다이브 결과의 무인증 임시 공유 스냅샷 — 로그인 게이트 밖에서 token 으로 조회.

    공유 생성 시점의 보고서를 payload_json 에 그대로 복사(스냅샷 고정)한다. 이후 종목이 재분석돼도
    공유 링크 내용은 불변. expires_at(생성+30분) 이후엔 조회 API 가 만료로 처리한다(행 삭제는 지연 GC).
    token 은 URL 세이프 난수(추측 불가). 종목당 다건 허용(공유할 때마다 새 스냅샷).
    """

    __tablename__ = "deepdive_share"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(
        String(43), unique=True, index=True
    )  # secrets.token_urlsafe(32)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    stock_name: Mapped[str | None] = mapped_column(String(120))  # 스냅샷 당시 종목명(표시용)
    payload_json: Mapped[dict] = mapped_column(JSONB)  # DeepDiveReportOut 스냅샷
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BusinessReportRaw(Base):
    """정기보고서(사업/반기/분기)에서 추출한 사업 내용 원문 — 접수번호·섹션별 캐시.

    DART document.xml 을 매번 재다운로드(수MB)하지 않도록 추출 결과를 보존한다. annual 은
    '사업의 내용' 전체(business_content), half/quarter 는 '회사의 개황'(company_overview)을 담는다.
    assemble_overview 가 이 원문을 읽어 투자자 관점으로 조립한다.
    """

    __tablename__ = "business_report_raw"
    __table_args__ = (
        UniqueConstraint("stock_code", "rcept_no", "section_id", name="uq_business_raw"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    rcept_no: Mapped[str] = mapped_column(String(14), index=True)  # 출처 접수번호
    report_kind: Mapped[str] = mapped_column(String(8))  # annual | half | quarter
    period: Mapped[str] = mapped_column(String(16))  # '2024.12' | '2025.03' ...
    section_id: Mapped[str] = mapped_column(String(24))  # business_content | company_overview
    text: Mapped[str] = mapped_column(Text, default="")  # 태그 제거한 본문
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BusinessOverviewCache(Base):
    """종목 사업 개요 조립 결과 캐시 — FinancialStatementCache 패턴(cache-aside).

    GET /companies/{code}/business 응답을 (stock_code) 당 1행 JSONB 로 캐시. 새 정기보고서
    감지 시 refresh_if_new_report 가 재조립해 갱신. 페이로드는 BusinessOverview 직렬화
    (sections + tables, 투자자 관점). inputs_hash 로 재생성 판정(원문 집합이 바뀌었는지).
    """

    __tablename__ = "business_overview_cache"
    __table_args__ = (UniqueConstraint("stock_code", name="uq_business_overview_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    stock_name: Mapped[str] = mapped_column(String(120), default="")
    # 베이스 사업보고서 접수번호(신선도·출처 표시).
    as_of_annual_rcept: Mapped[str] = mapped_column(String(14), default="")
    # 조립에 사용된 정기보고서 목록 [{rcept_no, kind, period, is_base}].
    source_reports: Mapped[list] = mapped_column(JSONB, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # BusinessOverview 직렬화
    # 원문 집합(rcept_no 모음) 해시 — 바뀌면 재조립. 빈 값/None 은 미조립.
    inputs_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BusinessResearchJob(Base):
    """사업 리서치 비동기 큐(Job). Phase 3a.

    리서치 결과는 별도 테이블이 아니라 BusinessOverviewCache.payload["research_summary"]에
    직접 저장(다음 방문 시 GET /business 로 자동 노출). 이 행은 lifecycle만 관리.
    """

    __tablename__ = "business_research_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), index=True)
    # 상태: pending(큐 대기), running(실행 중), done(성공, research_summary 저장됨), failed(실패)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    # 사용자 가이드라인 입력(자유형 질문/관심사).
    guideline: Mapped[str] = mapped_column(Text, default="")
    # 진행률(0-100). 스케줄러 폴링/상태폴링용.
    progress: Mapped[int] = mapped_column(default=0)
    # 사용한 LLM 모델.
    model: Mapped[str] = mapped_column(String(120), default="")
    # 실패 원인(실패 시에만).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 요청 시각.
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 실행 시작 시각(running 전환 시).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 완료 시각(done/failed).
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 같은 종목의 동시 진행 중 job을 방지(dedup 위해 enqueue 사용).
        # 인덱스는 상태폴링 효율성.
        Index("ix_business_research_stock_status", "stock_code", "status"),
    )
