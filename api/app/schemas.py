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
    rs_rating: int | None = None  # IBD RS Rating 1~99(전종목 대비 가격 모멘텀)
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


class StageFrame(BaseModel):
    """와인스타인 국면 한 프레임(단기/중기/장기). 지평별 봉단위·MA기간이 다르다."""

    frame: str  # short | mid | long
    bar: str  # day | week | month (프레임의 봉단위)
    period: int  # 네이티브 봉 기준 MA 기간(일50/주30/월40)
    stage: int | None  # 1~4
    label: str | None  # '② 상승' 등
    ma_dir: str | None  # rising | flat | falling
    quality: float | None  # 추세 깨끗함 0~100(shape 신뢰도)
    volume_signal: str | None  # accumulation | distribution | neutral (축적/분산)


class StageSegment(BaseModel):
    """중기 국면이 이어지는 구간(차트 배경밴드용)."""

    stage: int
    from_date: str  # YYYY-MM-DD
    to_date: str


class RelStrengthPoint(BaseModel):
    date: str
    value: float  # Mansfield MRP (0중심)


class ElliottPivot(BaseModel):
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str  # '0'~'5' 또는 '' (미라벨)


class ElliottView(BaseModel):
    """엘리엇 파동 추정(실험적) — ZigZag 피벗 + 선택적 5파 라벨."""

    pivots: list[ElliottPivot]
    labeled: bool  # 5파 라벨 노출 여부
    confidence: float  # 0~1
    note: str


class CompanyTrend(BaseModel):
    """기술적 추세 — 와인스타인 국면(3프레임) + Mansfield 상대강도 + IBD RS Rating."""

    stock_code: str
    benchmark: str  # 벤치마크 지수(KOSPI/KOSDAQ)
    stages: list[StageFrame]
    stage_segments: list[StageSegment]
    rs_series: list[RelStrengthPoint]
    rs_latest: float | None
    rs_outperforming: bool | None
    rs_rating: int | None = None  # IBD RS Rating 1~99(전종목 대비 백분위, 야간 배치)
    elliott: ElliottView | None = None  # 엘리엇 파동 추정(실험적)


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
    op_status: str | None  # 흑자전환|흑자지속|적자전환|적자지속
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


class JudgmentOut(BaseModel):
    """판단 요약 — 점수에서 도출한 사실 요약 + 신호. 투자 자문 아님(표시측 면책 노출)."""

    signal: str  # fit | watch | avoid | insufficient
    signal_label: str
    strengths: list[str]
    weaknesses: list[str]
    checks: list[str]


class CompanyAnalysis(BaseModel):
    stock_code: str
    stock_name: str | None
    market: str | None
    overall_score: float | None  # 3축 종합 0~100
    axes: list[AnalysisAxis]
    topdown: TopDownView | None
    judgment: JudgmentOut | None = None  # 판단 요약(강점·약점·확인·신호)
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


class HoldingIn(BaseModel):
    """보유종목 저장 입력."""

    shares: float
    avg_cost: float
    stop_loss: float | None = None
    note: str | None = None


class HoldingOut(BaseModel):
    """보유종목 응답 + 파생 계산(손익·손절 상태). 현재가 없으면 손익 필드는 None."""

    stock_code: str
    stock_name: str | None = None
    shares: float
    avg_cost: float
    stop_loss: float | None
    note: str | None
    updated_at: datetime | None = None
    current_price: float | None = None
    market_value: float | None = None
    cost_basis: float = 0.0
    pnl: float | None = None
    pnl_pct: float | None = None
    stop_status: str = "none"  # none | ok | near | hit


class PortfolioSummaryOut(BaseModel):
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float | None
    stop_hit: int
    stop_near: int


class SectorWeightOut(BaseModel):
    sector: str
    weight_pct: float


class PortfolioView(BaseModel):
    """포트폴리오 전체 뷰 — 보유목록 + 요약 + 섹터분산."""

    holdings: list[HoldingOut]
    summary: PortfolioSummaryOut
    sectors: list[SectorWeightOut]
