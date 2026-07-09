export type ReportCategory = "company" | "industry";

export type Sentiment = "BUY" | "SELL" | "HOLD";

export interface MarketBrief {
  market_date: string | null;
  summary: string;
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

export type Timeframe = "30m" | "day" | "month";

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
  op_turnaround: boolean; // 흑자전환 여부
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

export interface FinancialPeriod {
  period: string;
  is_estimate: boolean;
  revenue: number | null;
  operating_income: number | null;
  net_income: number | null;
  eps: number | null;
  per: number | null;
  pbr: number | null;
  roe: number | null;
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

export type ScreenerMarket = "KOSPI" | "KOSDAQ";

// score(성장스코어, 기본) · market_cap(시총 작은순) · rev_yoy(매출성장률) · momentum(3개월 수익률) · trading_value(거래대금) · change(등락률) · coverage(리포트 수)
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

export interface ScreenerRow {
  stock_code: string;
  stock_name: string;
  market: ScreenerMarket;
  close_price: number | null;
  change_pct: number | null;
  market_cap: number | null; // 원 단위(KRW)
  trading_value: number | null; // 거래대금, 원 단위
  momentum_3m: number | null; // 3개월 수익률%
  revenue_yoy: number | null; // 매출 YoY 비율 (0.28 = +28%)
  op_yoy: number | null; // 영업이익 YoY 비율
  op_turnaround: boolean; // 흑자전환 여부
  growth_score: number | null; // 0~100, sort=score일 때만 채워짐
  coverage_count: number; // 최근 90일 리포트 수, 커버리지 없으면 0
  recent_sentiment: "BUY" | "HOLD" | null; // 커버리지 있으면 BUY/HOLD, 없으면 null
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
