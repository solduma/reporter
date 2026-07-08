export type ReportCategory = "company" | "industry";

export type Sentiment = "BUY" | "SELL" | "HOLD";

export interface MarketBrief {
  market_date: string | null;
  summary: string;
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

// 관세청 수출입 무역통계 — HS 코드(문자열) → 한글 품목명
export type TradePresets = Record<string, string>;

export interface TradePoint {
  period: string; // "YYYY.MM"
  export_usd: number;
  import_usd: number;
  balance_usd: number;
}

export type ScreenerMarket = "KOSPI" | "KOSDAQ";

// market_cap(시총 작은순) · momentum(3개월 수익률, 데이터 준비중) · trading_value(거래대금) · change(등락률)
export type ScreenerSort = "market_cap" | "momentum" | "trading_value" | "change";

export interface ScreenerRow {
  stock_code: string;
  stock_name: string;
  market: ScreenerMarket;
  close_price: number | null;
  change_pct: number | null;
  market_cap: number | null; // 원 단위(KRW)
  trading_value: number | null; // 거래대금, 원 단위
  three_month_rate: number | null; // 3개월 수익률% — 현재는 항상 null (데이터 소스 미비)
}

export interface ScreenerResult {
  as_of: string | null;
  total: number;
  items: ScreenerRow[];
}

export type TimelineItemType = "report" | "disclosure";

export interface TimelineItem {
  type: TimelineItemType;
  date: string;
  title: string;
  source: string; // 증권사(리포트) 또는 제출인(공시)
  sentiment: Sentiment;
  rationale: string;
  link: string | null; // 리포트 read_url 또는 DART 뷰어 URL
  report_id: number | null; // 리포트면 PDF 조회용 id, 공시면 null
}
