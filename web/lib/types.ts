export type ReportCategory = "company" | "industry";

export type Sentiment = "BUY" | "SELL" | "HOLD";

export interface MarketBrief {
  market_date: string | null;
  summary: string;
  phase?: string; // forecast(개장 전) | intraday(장중) | closing(마감)
  updated_at?: string | null; // 마지막 갱신 시각(ISO)
}

// 미국 3대 지수 한 종목. 값은 표시용 문자열, rising: true=상승 · false=하락 · null=미확인
export interface UsIndex {
  name: string;
  close: string;
  change: string;
  change_ratio: string;
  rising: boolean | null;
}

export interface HotSector {
  sector: string;
  report_count: number;
  avg_sentiment: number; // -1..+1 (BUY+1 · HOLD 0 · SELL−1 평균)
}

export interface TradeSpark {
  hs: string;
  period: string; // "YYYY.MM"
  export_usd: number; // USD
}

export interface MarketOverview {
  market_date: string | null;
  us_indices: UsIndex[];
  kr_indices: UsIndex[];
  brief_summary: string;
  hot_sectors: HotSector[];
  trade_spark: TradeSpark[];
}

export interface StockSearchHit {
  stock_code: string;
  stock_name: string;
  market: string; // KOSPI | KOSDAQ
  market_cap: number | null; // 원
}

export interface AnalysisMetric {
  label: string;
  value: string;
}

export interface ScoreFactor {
  label: string; // 요소명 (예: "매출 YoY")
  value: string; // 원시값 표시
  norm: number | null; // 0~1 정규화값 (기여도 = norm*weight)
  weight: number; // 가중치
}

export interface AnalysisAxis {
  key: string; // growth | value | technical | topdown
  label: string;
  score: number | null; // 0~100
  metrics: AnalysisMetric[];
  method?: string | null; // 점수 계산 방식 설명(hover)
  factors?: ScoreFactor[]; // 점수 요소별 근거(값·정규화·가중치)
}

export interface TopDownView {
  kr_sector: string | null;
  kr_sector_flow: number | null;
  us_sector: string | null;
  us_sector_flow: number | null;
  us_sector_return_3m: number | null;
  kr_index_flow: number | null; // 종목 시장 지수(코스피/코스닥) 수급 점수(0~100)
  stock_rs: number | null; // 종목 상대강도(RS Rating 1~99) — 탑다운 종목 변별 항
  kr_indices: { name: string; change_ratio: string; rising: boolean | null }[];
}

export type JudgmentSignal = "fit" | "watch" | "avoid" | "insufficient";

export interface Judgment {
  signal: JudgmentSignal;
  signal_label: string;
  strengths: string[];
  weaknesses: string[];
  checks: string[];
}

export interface CompanyAnalysis {
  stock_code: string;
  stock_name: string | null;
  market: string | null;
  overall_score: number | null;
  axes: AnalysisAxis[];
  topdown: TopDownView | null;
  judgment?: Judgment | null;
  comment: string | null;
  comment_pending?: boolean; // true면 코멘트 백그라운드 생성 중 — 재조회로 채움
}

export interface Report {
  id: number;
  category: ReportCategory;
  title: string;
  broker: string;
  name: string | null;
  summary: string;
  sentiment: Sentiment;
  rationale: string;
  published_date: string;
  has_pdf: boolean;
}

export interface Industry {
  industry: string;
  report_count: number;
}

export interface SectorRow {
  sector: string;
  report_count: number;
  avg_sentiment: number; // -1..+1 (BUY+1 · HOLD 0 · SELL−1 평균)
  rotation_score: number; // 0..100
}

export type FlowMarket = "KR" | "US";

export type LookbackPeriod = "1d" | "1w" | "1m" | "3m" | "1y";

export interface SectorFlowRow {
  sector: string;
  market: FlowMarket;
  symbol: string;
  flow_score: number | null; // 0..100 자금유입 강도
  return_3m: number | null;
  near_high_pct: number | null;
  vol_ratio: number | null;
  foreign_delta: number | null; // 외국인비율 변화(pp), 국내만
}

export interface SectorFlowDetail {
  industry: string;
  kr: SectorFlowRow | null; // 매칭된 국내 섹터 ETF flow
  us: SectorFlowRow | null; // 대응 미국 섹터 ETF flow(선행)
}

export interface SectorStock {
  name: string;
  code: string | null; // 국내 6자리 코드(미국은 null — 종목분석 페이지 없음)
  symbol: string | null; // 차트 조회용 심볼(국내=코드, 미국=네이버 심볼)
  market: string; // KR | US
  close: string | null;
  change_ratio: string | null;
  rising: boolean | null;
}

export type SectorStockSort = "cap" | "value"; // 시총 | 거래대금

// /api/chart 는 30분봉을 지원하지 않는다(일/주/월만).
export type ChartTimeframe = "day" | "week" | "month";

// 차트 조회 대상 하나(심볼+시장+표시명). 프론트가 /api/chart 로 봉을 받아 그린다.
export interface ChartRef {
  label: string;
  symbol: string;
  market: FlowMarket;
}

// 섹터 상세 차트 구성 — 지수 쌍 + 국내/미국 섹터 추종 ETF.
export interface SectorChartMeta {
  industry: string;
  indices: ChartRef[]; // [코스피, QQQ, 코스닥, IWM]
  kr_etf: ChartRef | null;
  us_etf: ChartRef | null;
}

export interface ReportRef {
  id: number;
  title: string;
  broker: string;
  sentiment: Sentiment;
  summary: string;
  read_url: string | null;
  has_pdf: boolean;
}

export interface SentimentPoint {
  date: string;
  avg_sentiment: number;
  reports: ReportRef[];
}

export type Timeframe = "30m" | "day" | "week" | "month";

export interface CompanySummary {
  stock_code: string;
  stock_name: string | null;
}

export interface CompanyGrowth {
  stock_code: string;
  stock_name: string | null;
  market: string | null;
  market_cap: number | null; // 원 단위(KRW)
  close_price: number | null;
  change_pct: number | null; // 등락률 %
  momentum_3m: number | null; // 3개월 수익률 %
  revenue_yoy: number | null; // 매출 YoY 비율 (0.25 = +25%)
  op_yoy: number | null; // 영업이익 YoY 비율
  op_turnaround: boolean; // 흑자전환 여부(적자→흑자)
  op_status: string | null; // 흑자전환|흑자지속|적자전환|적자지속
  op_margin_delta: number | null; // 영업이익률 변화(0.559=+55.9pp)
  eps_yoy: number | null; // 주당순이익 YoY
  net_status: string | null; // 순이익 손익상태
  net_margin_delta: number | null; // 순이익률 변화
  ebitda_status: string | null; // EBITDA 손익상태
  ebitda_margin_delta: number | null; // EBITDA마진 변화
  period: string | null; // 기준 분기 "YYYY.MM"
  coverage_count: number; // 최근 90일 리포트 수
  buy_ratio: number | null; // 최근 90일 BUY 비율 0~1
}

export interface CandlePoint {
  t: string;
  o: number;
  h: number;
  low: number;
  c: number;
  v: number;
}

export interface StageFrame {
  frame: "short" | "mid" | "long";
  bar: "day" | "week" | "month"; // 프레임 봉단위
  period: number; // 네이티브 봉 기준 MA기간(일50/주30/월40)
  stage: number | null; // 1~4
  label: string | null; // '② 상승' 등
  ma_dir: "rising" | "flat" | "falling" | null;
  quality: number | null; // 추세 깨끗함 0~100(shape 신뢰도)
  volume_signal: "accumulation" | "distribution" | "neutral" | null;
  volatility: "contraction" | "expansion" | "normal" | null;
  low_confidence?: boolean; // 이력 부족(장기 프레임 등)이면 true
  channel_pos?: number | null; // Donchian 채널 내 위치 0~100(고점권=100)
  breakout?: "up" | "down" | "none" | null; // 신 N기간 고/저 돌파 + 볼륨 확인
  // 시장 구조(스윙 고·저) — 매수/매도 타점 근거
  structure?: "up" | "down" | "range" | "none" | null; // HH/HL/LH/LL 관계
  last_high?: "HH" | "LH" | "none" | null; // 최근 고점이 직전 대비
  last_low?: "HL" | "LL" | "none" | null; // 최근 저점이 직전 대비
  setup?: "stage1_to_2" | "stage3_to_4" | "none" | null; // 국면 전환 조짐 타점
  // 박스권(수평 지지/저항) + 최신봉 돌파/이탈 — 현재 매수/매도 타점
  box_support?: number | null; // 박스 하단(지지)
  box_resistance?: number | null; // 박스 상단(저항)
  box_event?: "breakout" | "breakdown" | "inside" | "none" | null;
  box_vol_confirmed?: boolean; // 돌파/이탈 봉 거래량 확정
}

export interface SecularView {
  ma_months: number | null; // 실제 사용한 MA 개월수
  position: "above" | "near" | "below" | null;
  ma_dir: "rising" | "flat" | "falling" | null;
  ratio: number | null; // 종가/secular MA - 1
}

export interface StageSegment {
  stage: number; // 1~4
  from_date: string; // YYYY-MM-DD
  to_date: string;
}

export interface RelStrengthPoint {
  date: string;
  value: number; // Mansfield MRP (0중심)
}

export interface CompanyTrend {
  stock_code: string;
  benchmark: string; // KOSPI | KOSDAQ
  stages: StageFrame[];
  stage_segments: StageSegment[]; // 하위호환(중기)
  segments_by_frame: Record<string, StageSegment[]>; // short/mid/long → 국면 구간
  rs_series: RelStrengthPoint[];
  rs_latest: number | null;
  rs_outperforming: boolean | null;
  rs_rating?: number | null; // IBD RS Rating 1~99
  elliott?: ElliottView | null; // 엘리엇 파동 추정(실험적)
  secular?: SecularView | null; // 장기 평균 대비 위치
}

export interface ElliottPivot {
  date: string; // YYYY-MM-DD
  price: number;
  kind: "high" | "low";
  label: string; // '0'~'5' 또는 ''
}

export interface ElliottWaveSegment {
  start_date: string; // YYYY-MM-DD
  end_date: string;
  start_price: number;
  end_price: number;
  phase: "motive" | "corrective";
  direction: "up" | "down"; // 실제 가격 진행 방향
  wave_label: string; // '1'~'5' | 'A'~'C'
  bars?: number; // 소요 봉 수
  confidence?: number; // 0~1
}

export interface ElliottProjection {
  wave: string; // 투영 대상(예: '다음 조정')
  low: number;
  high: number;
  bars_low?: number; // 예상 소요 봉 수 하한
  bars_high?: number; // 예상 소요 봉 수 상한
  basis: string; // 근거 문구
}

export interface ElliottView {
  pivots: ElliottPivot[]; // 세부(minor) 피벗(라벨 in-place)
  labeled: boolean; // 세그먼트를 하나라도 검출했는지
  confidence: number; // 0~1 (최근 세그먼트 신뢰도)
  direction?: "up" | "down" | "none"; // 최근 세그먼트 방향
  segments?: ElliottWaveSegment[]; // leg(연속 위상) + impulse(강조) 세그먼트
  current_position?: string; // 현재 파동 위치(추정 문구)
  invalidation_price?: number | null; // 현재 카운트 무효화 경계
  projection?: ElliottProjection | null; // 다음 파동 가격 목표 zone
  note: string;
}

export interface FinancialPeriod {
  period: string;
  is_estimate: boolean;
  revenue: number | null;
  operating_income: number | null;
  net_income: number | null;
  eps: number | null;
  bps?: number | null;
  per: number | null;
  pbr: number | null;
  psr?: number | null;
  roe: number | null;
  ev_ebitda?: number | null;
}

// 재무 백필 진행상태(GET /api/companies/{code}/financials/status). 가용분은 즉시 표시하고
// 이 상태로 '백필 중' 배지를 그린다.
export interface FinancialsStatus {
  fresh: boolean;
  financials_10y_done: boolean;
  report_10y_done: boolean;
}

export interface Peer {
  stock_code: string;
  name: string;
  price: string | null;
  market_cap: string | null;
  foreign_ratio: string | null;
  per: string | null;
  pbr: string | null;
  roe: string | null;
  ev_ebitda: string | null;
  psr: string | null;
  // 동일업종 4축·종합 점수(0~100, 종목분석과 동일 절대 밴드). 계산 불가 축은 null.
  overall_score: number | null;
  growth_score: number | null;
  value_score: number | null;
  trend_score: number | null;
  topdown_score: number | null;
}

// 관세청 수출입 무역통계 프리셋 — 4자리 대표품목 + 하위 6자리 세부품목.
// groups: {hs4: 명칭}, subitems: {hs4: {hs6: 명칭}} (세부품목 없는 대표품목은 subitems 키 부재)
export interface TradePresets {
  groups: Record<string, string>;
  subitems: Record<string, Record<string, string>>;
}

export interface TradePoint {
  period: string; // "YYYY.MM"
  export_usd: number;
  import_usd: number;
  balance_usd: number;
}

// KIS WebSocket 실시간 체결 틱(SSE /api/realtime/quote 로 push).
export interface RealtimeQuote {
  code: string;
  price: number;
  rising: boolean | null; // 상승 true · 하락 false · 보합/불명 null
  change: number;
  change_ratio: number; // 등락률 %
  volume: number; // 누적 거래량
  ts: string; // 체결 시각 HHMMSS
}

export type ScreenerMarket = "KOSPI" | "KOSDAQ";

// 스크리너 전략: overall(종합) · growth(성장) · value(가치) · trend(추세) · topdown(탑다운)
export type ScreenerStrategy = "overall" | "growth" | "value" | "trend" | "topdown";

// score(전략 스코어, 기본) · market_cap · rev_yoy · momentum · trading_value · change · coverage
export type ScreenerSort =
  | "score"
  | "market_cap"
  | "rev_yoy"
  | "momentum"
  | "trading_value"
  | "change"
  | "coverage";

// 영업이익 필터: turnaround(흑자전환) · growth(YoY 성장)
export type ScreenerOpGrowth = "turnaround" | "growth";

// 이벤트 유형 필터
export type ScreenerEventKind = "disclosure" | "report" | "surge" | "broadcast" | "news";

export interface ScreenerRow {
  stock_code: string;
  stock_name: string;
  market: ScreenerMarket;
  close_price: number | null;
  change_pct: number | null;
  market_cap: number | null; // 원 단위(KRW)
  trading_value: number | null; // 거래대금, 원 단위
  momentum_3m: number | null; // 3개월 수익률%
  rs_rating: number | null; // IBD RS Rating 1~99(전종목 대비 가격 모멘텀)
  revenue_yoy: number | null; // 매출 YoY 비율 (0.28 = +28%)
  op_yoy: number | null; // 영업이익 YoY 비율
  op_turnaround: boolean; // 흑자전환 여부
  growth_score: number | null; // 성장 스코어
  value_score: number | null; // 가치 스코어
  coverage_count: number; // 최근 90일 리포트 수, 커버리지 없으면 0
  recent_sentiment: "BUY" | "HOLD" | null; // 커버리지 있으면 BUY/HOLD, 없으면 null
  // 가치 전략(Financial 최신 분기)
  per: number | null;
  pbr: number | null;
  roe: number | null;
  ev_ebitda: number | null;
  div_yield: number | null; // 시가배당률(%)
  // 추세/탑다운
  trend_score: number | null; // 기술적 추세 종합 0~100
  topdown_score: number | null; // 섹터 수급(탑다운) 0~100
  kr_sector: string | null; // 대표 국내 섹터
  // 이벤트(모든 행 컬럼)
  event_kind: string | null; // 공시|리포트|급등락|브리핑|뉴스
  event_summary: string | null;
  event_date: string | null; // YYYY-MM-DD
  // 전략별 스코어(0~100). 어느 전략이든 채워진다.
  score: number | null;
}

export interface ScreenerResult {
  as_of: string | null;
  total: number;
  items: ScreenerRow[];
}

export type TimelineItemType = "report" | "disclosure" | "broadcast";

export interface TimelineItem {
  type: TimelineItemType;
  date: string;
  title: string;
  source: string; // 증권사(리포트) 또는 제출인(공시) 또는 "텔레그램 브리핑"
  sentiment: Sentiment;
  rationale: string;
  link: string | null; // 리포트 read_url 또는 DART 뷰어 URL
  report_id: number | null; // 리포트면 PDF 조회용 id, 공시면 null
  broadcast_id?: number | null; // 브로드캐스트면 상세 조회용 id
  kind?: string | null; // 브로드캐스트 종류(digest_market 등)
}

// 텔레그램으로 발송된 콘텐츠 종류
export type BroadcastKind =
  | "digest_market"
  | "digest_invest"
  | "digest_econ"
  | "digest_bond"
  | "closing"
  | "market_news"
  | "premarket"
  | "afternoon"
  | "morning"
  | "per_entity";

export interface BroadcastRef {
  id: number;
  kind: BroadcastKind;
  ref_date: string;
  sent_at: string;
  title: string;
  snippet: string;
  stock_codes: string[];
  industries: string[];
}

export interface BroadcastDetail {
  id: number;
  kind: BroadcastKind;
  ref_date: string;
  sent_at: string;
  title: string;
  body: string;
  source_refs: {
    reports?: { broker: string; title: string; url: string }[];
    news?: { title: string; url: string; source: string }[];
    keywords?: string[];
  };
  stock_codes: string[];
  industries: string[];
}

// US 종목 현재 시세 + 네이버 차트 심볼(/api/us/companies/{ticker}/quote)
export interface UsQuote {
  ticker: string;
  naver_symbol: string; // /api/chart?market=US 조회용
  name: string | null;
  close: number | null;
  change_ratio: string | null;
  rising: boolean | null;
}

// US 종목 재무 지표(SEC EDGAR 산출, /api/us/companies/{ticker}/financials)
export interface UsFinancial {
  ticker: string;
  name: string | null;
  ttm_revenue: number | null; // USD
  ttm_net_income: number | null;
  ttm_operating_income: number | null;
  ttm_eps: number | null;
  equity: number | null;
  shares: number | null;
  market_cap: number | null; // 근사(종가×주식수), USD
  per: number | null;
  pbr: number | null;
  psr: number | null;
  roe: number | null; // %
}

// US 스크리너 행(/api/us/screener)
export interface UsScreenerRow {
  ticker: string;
  name: string;
  exchange: string | null; // NASDAQ | NYSE
  sector: string | null;
  close_price: number | null; // USD
  change_pct: number | null;
  market_cap: number | null; // USD
  trading_value: number | null; // USD
  per: number | null;
  pbr: number | null;
  eps: number | null;
  momentum_3m: number | null; // %
  near_high_pct: number | null; // 52주 고점 근접 %
  has_recent_8k: boolean;
  score: number | null;
}

export interface UsScreenerResult {
  as_of: string | null;
  total: number;
  items: UsScreenerRow[];
}

export interface UsScreenerQuery {
  mktcapMin?: number;
  mktcapMax?: number;
  liqMin?: number;
  perMax?: number;
  pbrMax?: number;
  momMin?: number;
  exchange?: "NASDAQ" | "NYSE";
  sector?: string;
  hasEvent?: boolean;
  sort?: string;
  limit?: number;
  offset?: number;
}

// US 8-K 공시(/api/us/companies/{ticker}/disclosures)
export interface UsDisclosure {
  accession: string;
  form_type: string;
  filing_date: string;
  title: string | null;
  primary_doc_url: string;
  sentiment: string | null;
}

// 경제/실적 캘린더
export type CalendarRegion = "US" | "KR" | "GLOBAL";
export type CalendarKind = "macro" | "earnings" | "fomc" | "election" | "geo";

export interface CalendarEvent {
  event_date: string; // YYYY-MM-DD
  region: CalendarRegion;
  kind: CalendarKind;
  title: string;
  importance: number; // 1~3
  is_past: boolean;
  actual: string | null;
  previous: string | null;
  consensus: string | null;
  unit: string | null;
  impact_text: string | null; // 과거: 지수 영향·이유(LLM)
  impact_direction: "positive" | "negative" | "neutral" | null; // 과거: 지수 영향 방향(색칠)
  expectation_text: string | null; // 미래: 시장 기대치(LLM)
}

export interface CalendarView {
  as_of: string;
  past: CalendarEvent[]; // 최신순
  upcoming: CalendarEvent[]; // 임박순
}

// 종목 딥다이브
export type DeepDiveJobStatus = "none" | "pending" | "running" | "paused" | "done" | "failed";

export interface DeepDiveStatus {
  stock_code: string;
  status: DeepDiveJobStatus;
  current_stage: number; // 0~5
  progress: number; // 0~100
  error: string | null;
  has_report: boolean;
  hitl_pending: boolean; // 밸류에이션 직전 사용자 인풋 대기
  hitl_prompt: string | null; // 사용자에게 보일 질문
}

// 수치형 claim 의 기준치·증분·환산(에이전트가 리서치해 채움).
export interface HitlNumeric {
  baseline: number | null;
  new_value: number | null;
  unit: string | null;
  delta_pct: number | null;
  segment_revenue_share: number | null;
  conversion_chain: string | null;
}

// HITL 인풋 검증 결과 한 건(반박/반영/가능성).
export interface HitlClaim {
  claim: string;
  claim_type?: string; // fact_event | numeric
  refuted?: boolean | null; // 실제 스키마: 반박 못 하면 false(반영). verdict 라벨은 이걸로 유도.
  verdict?: string | null; // 구 스키마 하위호환(반박|반영|가능성). 신 스키마엔 결측.
  probability?: number | null; // 0~1 (결측 가능)
  evidence?: string | null;
  reasoning?: string | null;
  numeric?: HitlNumeric | null; // numeric claim 일 때만
  valuation_impact?: string | null;
}

export interface HitlResult {
  claims: HitlClaim[];
  summary: string | null;
  _procedure_incomplete?: boolean; // 절차 미완료(기준치·환산 미완) 마킹
}

// 예상 이익 성장률 앙상블 성분(외삽 시). 각 %.
export interface ForwardComponents {
  avg3y_pct: number;
  recent_pct: number;
  convex_pct: number;
}

// 지표별 forward(예상) 이익 고지. source: hitl|consensus|extrapolation.
export interface ForwardMetric {
  source: string;
  base_ttm?: number;
  base_annual?: number;
  forward: number;
  growth_pct?: number | null;
  capped?: boolean;
  components?: ForwardComponents;
  yoy_samples?: number;
}

export interface ForwardMeta {
  source?: string; // 최상위 소스(hitl 일괄 반영 시)
  eps?: ForwardMetric;
  ebitda?: ForwardMetric;
}

// 밸류에이션 방식 하나(PER·PBR·EV/EBITDA·DCF·DDM).
export interface ValuationMethod {
  method: string; // 기계 식별자
  label: string; // 표시명
  applicable: boolean; // 계산 성공 여부
  target_price: number | null;
  upside_pct: number | null;
  confidence: string; // 상|중|하
  assumptions: Record<string, unknown>;
  process: string[]; // 계산 과정 스텝
  note: string; // 근거·제외 사유
}

// 5단계 밸류에이션 결과(다중 방식 blend). 구 스키마와 구분되도록 methods 로 판별.
export interface ValuationResult {
  final_target_price: number | null;
  final_upside_pct: number | null;
  current_price: number | null;
  method_count: number;
  stock_type?: string | null; // growth|asset|financial|cyclical|other — 방식 가중 근거
  method_fit?: Record<string, number> | null; // 방식별 적합도(0=제외)
  forward_meta?: ForwardMeta | null; // 예상 이익 소스·성장률 고지(EPS·EBITDA)
  entry_case: string | null;
  conclusion: string | null;
  methods: ValuationMethod[];
}

export interface DeepDiveReport {
  stock_code: string;
  model: string | null;
  overview: Record<string, unknown> | null;
  redflags: Record<string, unknown> | null;
  business: Record<string, unknown> | null;
  thesis: Record<string, unknown> | null;
  hitl: (HitlResult & Record<string, unknown>) | null;
  valuation: (ValuationResult & Record<string, unknown>) | Record<string, unknown> | null;
  narrative_md: string | null;
  verdict: string | null;
  upside_pct: number | null;
  as_of: string | null;
}

// 공유 링크 생성 응답 — token 으로 /share/{token} 조립.
export interface DeepDiveShare {
  token: string;
  expires_at: string;
}

// 주담(IR) 인터뷰 전략 — 아이템→질문 트리.
export interface IrInterviewQuestion {
  q: string;
  intent: string; // 왜 묻는가
  valuation_link: string; // 연결된 밸류 가정
  expected_signal: string; // 답변→목표가 방향
}

export interface IrInterviewItem {
  item: string;
  why_matters: string;
  linked_valuation_assumption: string;
  questions: IrInterviewQuestion[];
}

export interface IrInterviewStrategy {
  strategy_items: IrInterviewItem[];
  total_questions: number;
}

export interface IrInterviewStatus {
  stock_code: string;
  status: "pending" | "running" | "done" | "failed" | "none";
  progress: number;
  error: string | null;
  has_report: boolean;
}

export interface IrInterviewReport {
  stock_code: string;
  stock_name: string | null;
  model: string | null;
  strategy: IrInterviewStrategy | null;
  total_questions: number;
  as_of: string | null;
}

export interface IrInterviewListItem {
  stock_code: string;
  stock_name: string | null;
  total_questions: number;
  as_of: string | null;
}

// 무인증 공유 페이지가 받는 스냅샷(생성 시점 고정).
export interface SharedDeepDive {
  stock_code: string;
  stock_name: string | null;
  report: DeepDiveReport;
  created_at: string;
  expires_at: string;
}
