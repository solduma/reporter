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
    value_score: float | None = None  # 0~100
    # 가치 전략(Financial 최신 분기)
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    ev_ebitda: float | None = None
    div_yield: float | None = None  # 시가배당률(%)
    # 추세/탑다운 전략(사전계산·섹터 flow)
    trend_score: float | None = None  # 0~100 기술적 추세 종합
    topdown_score: float | None = None  # 0~100 섹터 수급(탑다운)
    kr_sector: str | None = None  # 대표 국내 섹터(탑다운 표시)
    # 이벤트(최근 이벤트 요약) — 모든 행에 컬럼으로 표시(별도 탭 제거)
    event_kind: str | None = None  # 대표 이벤트 유형(공시|리포트|급등락|브리핑|뉴스)
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
    volatility: str | None  # contraction | expansion | normal (ATR 변동성 레짐)
    low_confidence: bool = False  # 이력 부족(리샘플 봉 < 최소치)이면 True
    channel_pos: float | None = None  # Donchian 채널 내 위치 0~100(고점권=100)
    breakout: str | None = None  # up | down | none (신 N기간 고/저 돌파 + 볼륨 확인)
    # 시장 구조(스윙 고·저) — 매수/매도 타점 근거.
    structure: str | None = None  # up | down | range | none (HH/HL/LH/LL 관계)
    last_high: str | None = None  # HH | LH | none (최근 고점이 직전 대비)
    last_low: str | None = None  # HL | LL | none (최근 저점이 직전 대비)
    setup: str | None = None  # stage1_to_2 | stage3_to_4 | none (국면 전환 조짐 타점)
    # 박스권(수평 지지/저항) + 최신봉 돌파/이탈 이벤트 — 현재 매수/매도 타점.
    box_support: float | None = None  # 박스 하단(지지)
    box_resistance: float | None = None  # 박스 상단(저항)
    box_event: str | None = None  # breakout | breakdown | inside | none (최신봉 vs 박스경계)
    box_vol_confirmed: bool = False  # 돌파/이탈 봉 거래량이 평균 대비 확정 배수↑


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


class ElliottWaveSegment(BaseModel):
    """엘리엇 파동 한 개(=피벗 사이 한 다리). 반복 사이클 1-2-3-4-5-A-B-C 중 하나."""

    start_date: str  # YYYY-MM-DD
    end_date: str
    start_price: float
    end_price: float
    phase: str  # motive | corrective
    direction: str  # up | down (실제 가격 진행 방향)
    wave_label: str  # '1'~'5' | 'A'~'C'
    bars: int = 0  # 소요 봉 수
    confidence: float = 0.0  # 0~1


class ElliottProjection(BaseModel):
    """다음 파동 가격 목표 구간(피보 투영). 단일 선이 아닌 zone."""

    wave: str  # 투영 대상(예: '다음 조정' | '다음 추진')
    low: float  # 가격 하한
    high: float  # 가격 상한
    bars_low: int = 0  # 예상 소요 봉 수 하한
    bars_high: int = 0  # 예상 소요 봉 수 상한
    basis: str  # 근거 문구


class ElliottView(BaseModel):
    """엘리엇 파동 추정(실험적) — 전 구간 연속 위상 교대 + 강조 5파 + 현재 위치 + 가격 투영."""

    pivots: list[ElliottPivot]  # 기본 다리 피벗(스윙 흐름)
    labeled: bool  # 강조 임펄스를 하나라도 검출했는지
    confidence: float  # 0~1 (최근 임펄스 신뢰도)
    direction: str = "none"  # up | down | none (최근 임펄스 방향)
    segments: list[ElliottWaveSegment] = []  # leg(연속 위상) + impulse(강조) 세그먼트
    current_position: str = ""  # 현재 파동 위치(추정 문구)
    invalidation_price: float | None = None  # 현재 카운트 무효화 경계
    projection: ElliottProjection | None = None  # 다음 파동 가격 목표 zone
    note: str


class SecularView(BaseModel):
    """장기 평균(secular) 대비 위치 — 데이터 허락 최장 월봉 MA. 전환 프레임과 직교한 맥락."""

    ma_months: int | None  # 실제 사용한 MA 개월수
    position: str | None  # above | near | below
    ma_dir: str | None  # rising | flat | falling
    ratio: float | None  # 종가/secular MA - 1


class CompanyTrend(BaseModel):
    """기술적 추세 — 와인스타인 국면(3프레임) + Mansfield 상대강도 + IBD RS Rating."""

    stock_code: str
    benchmark: str  # 벤치마크 지수(KOSPI/KOSDAQ)
    stages: list[StageFrame]
    stage_segments: list[StageSegment]  # 하위호환: 중기 국면 구간
    segments_by_frame: dict[str, list[StageSegment]]  # frame(short/mid/long) → 국면 구간
    rs_series: list[RelStrengthPoint]
    rs_latest: float | None
    rs_outperforming: bool | None
    rs_rating: int | None = None  # IBD RS Rating 1~99(전종목 대비 백분위, 야간 배치)
    elliott: ElliottView | None = None  # 엘리엇 파동 추정(실험적)
    secular: SecularView | None = None  # 장기 평균 대비 위치


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


class FinancialsStatusOut(BaseModel):
    """재무 화면 백필 진행상태 — 가용분은 /financials 로 즉시 표시하고, 이 상태로 배지/스피너를 그린다.

    fresh: 최근 분기 재무가 TTL 내 최신인지. financials_10y_done/report_10y_done: 10년 재무·
    보고서원문(EV/EBITDA) 백필이 이 종목에 대해 완료됐는지. 진행 중이면 프론트가 '백필 중' 표기.
    """

    fresh: bool
    financials_10y_done: bool
    report_10y_done: bool


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
    # 동일업종 4축·종합 점수(0~100, 종목분석과 동일 절대 밴드). 계산 불가 축은 None.
    overall_score: float | None = None
    growth_score: float | None = None
    value_score: float | None = None
    trend_score: float | None = None
    topdown_score: float | None = None


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
    op_margin_delta: float | None = None  # 영업이익률 변화(0.559=+55.9pp)
    eps_yoy: float | None = None  # 주당순이익 YoY
    # 순이익·EBITDA 도 영업이익과 동일하게 손익상태 + 마진 증감으로 성장 지표에 표시.
    net_status: str | None = None
    net_margin_delta: float | None = None
    ebitda_status: str | None = None
    ebitda_margin_delta: float | None = None
    period: str | None
    coverage_count: int  # 최근 1년 리포트 수
    buy_ratio: float | None  # 최근 1년 BUY 비율


class ScoreFactor(BaseModel):
    """축 점수 한 요소의 계산 근거 — hover 팝업으로 '어떻게 계산됐는지' 노출."""

    label: str  # 요소명 (예: "매출 YoY")
    value: str  # 원시값 표시 (예: "+32%")
    norm: float | None  # 0~1 정규화값 (기여도 = norm*weight)
    weight: float  # 가중치


class AnalysisAxis(BaseModel):
    """분석 한 축(성장/가치/추세/탑다운)의 점수와 근거 지표."""

    key: str  # growth | value | technical | topdown
    label: str  # 성장 등 표시명
    score: float | None  # 0~100 (계산 불가 시 None)
    metrics: list[dict]  # [{label, value}] 표시용 지표
    method: str | None = None  # 점수 계산 방식 설명(hover)
    factors: list[ScoreFactor] = []  # 점수 요소별 근거(값·정규화·가중치)


class TopDownView(BaseModel):
    """지수 → 섹터 → 종목 흐름. 국내/미국 동일섹터 수급 flow(미국 선행) + 국내 지수."""

    kr_sector: str | None  # 종목의 대표 국내 섹터
    kr_sector_flow: float | None  # 그 섹터의 국내 ETF 자금유입 강도(0~100)
    us_sector: str | None  # 대응 미국 섹터
    us_sector_flow: float | None  # 미국 섹터 ETF flow(선행)
    us_sector_return_3m: float | None
    kr_index_flow: float | None = None  # 종목 시장 지수(코스피/코스닥) 수급 점수(0~100)
    stock_rs: float | None = None  # 종목 상대강도(RS Rating 1~99) — 탑다운 종목 변별 항
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


class CalendarEventOut(BaseModel):
    """캘린더 이벤트 한 건. is_past 로 과거/미래 구분, 그에 따라 impact/expectation 중 하나가 찬다."""

    event_date: date
    region: str  # US | KR | GLOBAL
    kind: str  # macro | earnings | fomc | election | geo
    title: str
    importance: int  # 1~3
    is_past: bool
    actual: str | None = None
    previous: str | None = None
    consensus: str | None = None
    unit: str | None = None
    impact_text: str | None = None  # 과거: 지수 영향·이유(LLM)
    impact_direction: str | None = None  # 과거: 지수 영향 방향 positive|negative|neutral(색칠)
    expectation_text: str | None = None  # 미래: 시장 기대치(LLM)


class CalendarView(BaseModel):
    """캘린더 조회 결과 — 조회 구간 + 과거/미래로 나눈 이벤트 목록."""

    as_of: date
    past: list[CalendarEventOut]  # 최신순(가까운 과거 먼저)
    upcoming: list[CalendarEventOut]  # 임박순(가까운 미래 먼저)


class DeepDiveStatus(BaseModel):
    """딥다이브 job 진행 상태(프론트 폴링)."""

    stock_code: str
    status: str  # pending|running|paused|done|failed|none
    current_stage: int  # 0~5
    progress: int  # 0~100
    error: str | None = None
    has_report: bool = False  # 완료 보고서 존재 여부
    hitl_pending: bool = False  # 밸류에이션 직전 사용자 인풋 대기(프론트가 입력창 노출)
    hitl_prompt: str | None = None  # 사용자에게 보일 질문(지금까지 파악한 내용 요약)


class HitlInput(BaseModel):
    """HITL 사용자 인풋 제출 바디. 공백이면 '건너뜀'(검증 없이 밸류에이션 진행)."""

    input: str = ""


class DeepDiveReportOut(BaseModel):
    """딥다이브 보고서 — 단계별 구조화 JSON + 통합 서술 본문."""

    stock_code: str
    model: str | None = None
    overview: dict | None = None
    redflags: dict | None = None
    business: dict | None = None
    thesis: dict | None = None
    hitl: dict | None = None  # 사용자 인풋 검증 결과(반박/반영/가능성)
    valuation: dict | None = None
    narrative_md: str | None = None
    verdict: str | None = None
    upside_pct: float | None = None
    as_of: datetime | None = None


class IrInterviewStatus(BaseModel):
    """주담(IR) 인터뷰 job 진행 상태(프론트 폴링)."""

    stock_code: str
    status: str  # pending|running|done|failed|none
    progress: int  # 0~100
    error: str | None = None
    has_report: bool = False


class IrInterviewReportOut(BaseModel):
    """주담 인터뷰 전략 결과 — 아이템→질문 트리 + 종목 메타."""

    stock_code: str
    stock_name: str | None = None
    model: str | None = None
    strategy: dict | None = None  # {strategy_items: [...], total_questions: N}
    total_questions: int = 0
    as_of: datetime | None = None


class IrInterviewListItem(BaseModel):
    """주담 전략 목록 항목(최상단 메뉴 목록용)."""

    stock_code: str
    stock_name: str | None = None
    total_questions: int = 0
    as_of: datetime | None = None


class DeepDiveShareOut(BaseModel):
    """공유 링크 생성 응답 — 프론트가 /share/{token} URL 을 조립."""

    token: str
    expires_at: datetime


class DeepDiveSharedReport(BaseModel):
    """무인증 공유 페이지가 조회하는 스냅샷 — 생성 시점 보고서 + 종목명 + 만료 시각."""

    stock_code: str
    stock_name: str | None = None
    report: DeepDiveReportOut
    created_at: datetime
    expires_at: datetime
