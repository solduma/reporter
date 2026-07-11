"""API 응답 스키마 (Pydantic)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class MarketBrief(BaseModel):
    market_date: date | None
    summary: str
    phase: str = ""  # forecast(개장 전)/intraday(장중)/closing(마감) — 웹 국면 배지용
    updated_at: datetime | None = None  # 마지막 갱신 시각 — "장중 · HH:MM 기준" 표시


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


class ScreenerRow(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    close_price: int | None
    change_pct: float | None
    market_cap: int | None  # 원
    trading_value: int | None
    momentum_3m: float | None  # 3개월 수익률%
    # 성장 전략
    revenue_yoy: float | None  # 매출 YoY (0.28=+28%)
    op_yoy: float | None
    op_turnaround: bool
    coverage_count: int  # 최근 90일 리포트 수
    recent_sentiment: str | None  # 최근 리포트 센티먼트 BUY/SELL/HOLD
    growth_score: float | None  # 0~100
    # 가치 전략(Financial 최신 분기)
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    ev_ebitda: float | None = None
    div_yield: float | None = None  # 시가배당률(%)
    # 이벤트 전략(최근 이벤트 요약)
    event_kind: str | None = None  # 대표 이벤트 유형(공시|리포트|급등락|브리핑)
    event_summary: str | None = None  # 한 줄 요약
    event_date: date | None = None  # 이벤트 발생일
    # 전략별 스코어(0~100). 어느 전략이든 이 필드에 담아 정렬한다.
    score: float | None = None


class ScreenerResult(BaseModel):
    as_of: date | None
    total: int
    items: list[ScreenerRow]


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


class StockSearchHit(BaseModel):
    stock_code: str
    stock_name: str
    market: str  # KOSPI | KOSDAQ
    market_cap: int | None  # 원


class SectorStock(BaseModel):
    """섹터 소속 종목 + 시세. code=국내 6자리(종목분석 이동), symbol/market=차트 조회용."""

    name: str
    code: str | None  # 국내 6자리 코드(미국은 None — 종목분석 페이지 없음)
    symbol: str | None  # 차트 조회 심볼(국내=코드, 미국=네이버 심볼)
    market: str  # KR | US
    close: str | None  # 표시용 종가 문자열
    change_ratio: str | None  # 등락률 %
    rising: bool | None  # 상승/하락/판단불가


class FinancialPeriodOut(BaseModel):
    period: str
    is_estimate: bool
    revenue: float | None
    operating_income: float | None
    net_income: float | None
    eps: float | None
    bps: float | None = None  # PBR 밴드 계산용(#139)
    per: float | None
    pbr: float | None
    psr: float | None = None
    roe: float | None
    ev_ebitda: float | None = None
    dps: float | None = None  # 주당배당금(원)
    div_yield: float | None = None  # 시가배당률(%)


class PeerOut(BaseModel):
    stock_code: str
    name: str
    price: str | None
    market_cap: str | None
    foreign_ratio: str | None
    per: str | None
    pbr: str | None
    roe: str | None
    ev_ebitda: str | None = None
    psr: str | None = None


class CompanyGrowth(BaseModel):
    stock_code: str
    stock_name: str | None
    market: str | None
    market_cap: int | None
    close_price: int | None
    change_pct: float | None
    momentum_3m: float | None
    revenue_yoy: float | None
    op_yoy: float | None
    op_turnaround: bool
    period: str | None
    coverage_count: int
    buy_ratio: float | None  # 최근 90일 BUY 비율


class AnalysisAxis(BaseModel):
    """분석 한 축(성장/기술/탑다운)의 점수와 근거 지표."""

    key: str  # growth | technical | topdown
    label: str  # 성장 등 표시명
    score: float | None  # 0~100 (계산 불가 시 None)
    metrics: list[dict]  # [{label, value}] 표시용 지표


class TopDownView(BaseModel):
    """지수 → 섹터 → 종목 흐름. 국내/미국 동일섹터 수급 flow(미국 선행) + 국내 지수."""

    kr_sector: str | None  # 종목의 대표 국내 섹터
    kr_sector_flow: float | None  # 그 섹터의 국내 ETF 자금유입 강도(0~100)
    us_sector: str | None  # 대응 미국 섹터
    us_sector_flow: float | None  # 미국 섹터 ETF flow(선행)
    us_sector_return_3m: float | None
    kr_indices: list[dict]  # [{name, change_ratio, rising}]


class SectorFlowDetail(BaseModel):
    """한 섹터(산업)의 국내 ETF flow + 대응 미국 ETF flow(선행). 섹터 상세 페이지용."""

    industry: str  # 요청한 산업명
    kr: SectorFlowRow | None  # 매칭된 국내 섹터 ETF flow (매칭 실패 시 None)
    us: SectorFlowRow | None  # 대응 미국 섹터 ETF flow


class ChartRef(BaseModel):
    """차트 조회 대상 하나(심볼+시장+표시명). 프론트가 /api/chart 로 봉을 받아 그린다."""

    label: str
    symbol: str
    market: str  # KR | US


class SectorChartMeta(BaseModel):
    """섹터 상세 차트 구성 — 지수 쌍 + 국내/미국 섹터 추종 ETF. 종목 Top10 은 /stocks 사용."""

    industry: str
    indices: list[ChartRef]  # 지수(코스피/QQQ, 코스닥/IWM)
    kr_etf: ChartRef | None  # 국내 섹터 추종 ETF
    us_etf: ChartRef | None  # 미국 섹터 추종 ETF


class CompanyAnalysis(BaseModel):
    stock_code: str
    stock_name: str | None
    market: str | None
    overall_score: float | None  # 3축 종합 0~100
    axes: list[AnalysisAxis]
    topdown: TopDownView | None
    comment: str | None  # LLM 종합 코멘트(캐시 히트 시 값, 키 없으면 None)
    comment_pending: bool = False  # True 면 백그라운드 생성 중 — 프론트가 재조회로 채운다


class SectorRow(BaseModel):
    sector: str
    report_count: int
    avg_sentiment: float  # BUY+1/HOLD0/SELL-1 평균
    rotation_score: float  # 0~100


class SectorFlowRow(BaseModel):
    """수급 기반 섹터 로테이션 한 행(섹터 ETF)."""

    sector: str
    market: str  # KR | US
    symbol: str
    flow_score: float | None  # 0~100 자금유입 강도
    return_3m: float | None
    near_high_pct: float | None
    vol_ratio: float | None
    foreign_delta: float | None  # 외국인비율 변화(pp), 국내만


class MarketOverview(BaseModel):
    market_date: date | None
    us_indices: list[dict]  # {name, close, change, change_ratio, rising}
    kr_indices: list[dict]  # {name, close, change, change_ratio, rising}
    brief_summary: str
    hot_sectors: list[dict]  # {sector, report_count, avg_sentiment}
    trade_spark: list[dict]  # {hs, period, export_usd}


class TimelineItem(BaseModel):
    type: str  # 'report' | 'disclosure' | 'broadcast'
    date: date
    title: str
    source: str  # 증권사(리포트) 또는 제출인(공시)
    sentiment: str  # BUY | SELL | HOLD
    rationale: str
    link: str | None
    report_id: int | None = None  # 리포트면 PDF 조회용 id
    broadcast_id: int | None = None  # 브로드캐스트면 상세 조회용 id
    kind: str | None = None  # 브로드캐스트 종류(digest_market 등)


class BroadcastRef(BaseModel):
    """브로드캐스트 목록 항목(본문 미포함, snippet 만)."""

    id: int
    kind: str
    ref_date: date
    sent_at: datetime
    title: str
    snippet: str  # body 앞부분 미리보기
    stock_codes: list[str]
    industries: list[str]


class BroadcastDetail(BaseModel):
    id: int
    kind: str
    ref_date: date
    sent_at: datetime
    title: str
    body: str
    source_refs: dict
    stock_codes: list[str]
    industries: list[str]


class UsFinancialOut(BaseModel):
    ticker: str
    name: str | None
    ttm_revenue: float | None  # USD
    ttm_net_income: float | None
    ttm_operating_income: float | None
    ttm_eps: float | None
    equity: float | None
    shares: float | None
    market_cap: float | None  # 근사(종가 x 주식수), USD
    per: float | None
    pbr: float | None
    psr: float | None
    roe: float | None  # %


class UsQuoteOut(BaseModel):
    ticker: str
    naver_symbol: str  # 차트 조회용(/api/chart?market=US)
    name: str | None
    close: float | None
    change_ratio: str | None
    rising: bool | None


class UsScreenerRow(BaseModel):
    ticker: str
    name: str
    exchange: str | None  # NASDAQ | NYSE
    sector: str | None
    close_price: float | None  # USD
    change_pct: float | None
    market_cap: float | None  # USD
    trading_value: float | None  # USD
    per: float | None
    pbr: float | None
    eps: float | None
    momentum_3m: float | None  # %
    near_high_pct: float | None  # 52주 고점 근접도 %
    has_recent_8k: bool  # 최근 이벤트(8-K) 유무 — 이벤트 스크리너용
    score: float | None  # 0~100 (저평가·모멘텀 종합)


class UsScreenerResult(BaseModel):
    as_of: str | None
    total: int
    items: list[UsScreenerRow]


class UsDisclosureOut(BaseModel):
    accession: str
    form_type: str
    filing_date: date
    title: str | None  # 8-K item 요약
    primary_doc_url: str
    sentiment: str | None
