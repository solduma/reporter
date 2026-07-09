import type {
  BroadcastDetail,
  BroadcastKind,
  BroadcastRef,
  CandlePoint,
  CompanyGrowth,
  CompanySummary,
  FinancialPeriod,
  Industry,
  MarketBrief,
  MarketOverview,
  Peer,
  Report,
  ReportCategory,
  ScreenerMarket,
  ScreenerOpGrowth,
  ScreenerResult,
  ScreenerSort,
  SectorRow,
  SentimentPoint,
  StockSearchHit,
  Timeframe,
  TimelineItem,
  TradePoint,
  TradePresets,
} from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} 실패 (${res.status})`);
  }
  return (await res.json()) as T;
}

export function fetchMarketBrief(): Promise<MarketBrief> {
  return getJson<MarketBrief>("/api/today/market");
}

export function fetchMarketOverview(): Promise<MarketOverview> {
  return getJson<MarketOverview>("/api/market/overview");
}

export function searchStocks(q: string, limit = 10): Promise<StockSearchHit[]> {
  return getJson<StockSearchHit[]>(
    `/api/companies/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
}

export function fetchReports(category: ReportCategory): Promise<Report[]> {
  return getJson<Report[]>(`/api/today/reports?category=${category}`);
}

export function reportPdfUrl(id: number): string {
  return apiUrl(`/api/reports/${id}/pdf`);
}

export function fetchIndustries(): Promise<Industry[]> {
  return getJson<Industry[]>("/api/industries");
}

// rotation_score 내림차순으로 정렬되어 반환된다.
export function fetchSectors(): Promise<SectorRow[]> {
  return getJson<SectorRow[]>("/api/sectors");
}

export function fetchIndustrySentiment(
  name: string,
  range?: { from?: string; to?: string },
): Promise<SentimentPoint[]> {
  const params = new URLSearchParams();
  if (range?.from) {
    params.set("from", range.from);
  }
  if (range?.to) {
    params.set("to", range.to);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return getJson<SentimentPoint[]>(
    `/api/industries/${encodeURIComponent(name)}/sentiment${suffix}`,
  );
}

export interface ScreenerQuery {
  mktcapMax?: number;
  mktcapMin?: number;
  revYoyMin?: number;
  opGrowth?: ScreenerOpGrowth;
  momMin?: number;
  momMax?: number;
  liqMin?: number;
  market?: ScreenerMarket | "";
  coverage?: "has" | "none";
  recentBuy?: boolean;
  includeEtf?: boolean;
  sort?: ScreenerSort;
  limit?: number;
  offset?: number;
}

export function fetchScreener(query: ScreenerQuery): Promise<ScreenerResult> {
  const params = new URLSearchParams();
  const set = (key: string, value: number | string | undefined | null) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  };
  set("mktcap_max", query.mktcapMax);
  set("mktcap_min", query.mktcapMin);
  set("rev_yoy_min", query.revYoyMin);
  set("op_growth", query.opGrowth);
  set("mom_min", query.momMin);
  set("mom_max", query.momMax);
  set("liq_min", query.liqMin);
  set("market", query.market);
  set("coverage", query.coverage);
  if (query.recentBuy) {
    params.set("recent_buy", "true");
  }
  if (query.includeEtf) {
    params.set("include_etf", "true");
  }
  set("sort", query.sort);
  set("limit", query.limit);
  set("offset", query.offset);
  return getJson<ScreenerResult>(`/api/screener?${params.toString()}`);
}

export function fetchCompanySummary(code: string): Promise<CompanySummary> {
  return getJson<CompanySummary>(`/api/companies/${encodeURIComponent(code)}/summary`);
}

export function fetchCompanyGrowth(code: string): Promise<CompanyGrowth> {
  return getJson<CompanyGrowth>(`/api/companies/${encodeURIComponent(code)}/growth`);
}

export function fetchCandles(code: string, tf: Timeframe): Promise<CandlePoint[]> {
  return getJson<CandlePoint[]>(
    `/api/companies/${encodeURIComponent(code)}/candles?tf=${tf}`,
  );
}

export function fetchFinancials(code: string): Promise<FinancialPeriod[]> {
  return getJson<FinancialPeriod[]>(`/api/companies/${encodeURIComponent(code)}/financials`);
}

export function fetchPeers(code: string): Promise<Peer[]> {
  return getJson<Peer[]>(`/api/companies/${encodeURIComponent(code)}/peers`);
}

export function fetchTradePresets(): Promise<TradePresets> {
  return getJson<TradePresets>("/api/trade/presets");
}

export function fetchTrade(hs: string, start: string, end: string): Promise<TradePoint[]> {
  const params = new URLSearchParams({ hs, start, end });
  return getJson<TradePoint[]>(`/api/trade?${params.toString()}`);
}

export function fetchTimeline(
  code: string,
  range?: { from?: string; to?: string },
): Promise<TimelineItem[]> {
  const params = new URLSearchParams();
  if (range?.from) {
    params.set("from", range.from);
  }
  if (range?.to) {
    params.set("to", range.to);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return getJson<TimelineItem[]>(
    `/api/companies/${encodeURIComponent(code)}/timeline${suffix}`,
  );
}

export interface BroadcastQuery {
  industry?: string;
  stock?: string;
  kind?: BroadcastKind;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export function fetchBroadcasts(query: BroadcastQuery = {}): Promise<BroadcastRef[]> {
  const params = new URLSearchParams();
  const set = (key: string, value: string | number | undefined) => {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  };
  set("industry", query.industry);
  set("stock", query.stock);
  set("kind", query.kind);
  set("from", query.from);
  set("to", query.to);
  set("limit", query.limit);
  set("offset", query.offset);
  const q = params.toString();
  return getJson<BroadcastRef[]>(`/api/broadcasts${q ? `?${q}` : ""}`);
}

export function fetchBroadcast(id: number): Promise<BroadcastDetail> {
  return getJson<BroadcastDetail>(`/api/broadcasts/${id}`);
}
