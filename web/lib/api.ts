import type {
  BroadcastDetail,
  BroadcastKind,
  BroadcastRef,
  CandlePoint,
  ChartTimeframe,
  CompanyAnalysis,
  CompanyGrowth,
  CompanySummary,
  CompanyTrend,
  FinancialPeriod,
  FlowMarket,
  Holding,
  HoldingInput,
  Industry,
  MarketBrief,
  MarketOverview,
  Peer,
  PortfolioView,
  Report,
  ReportCategory,
  ScreenerMarket,
  ScreenerOpGrowth,
  ScreenerResult,
  ScreenerSort,
  ScreenerStrategy,
  SectorChartMeta,
  SectorFlowDetail,
  SectorFlowRow,
  SectorStock,
  SectorStockSort,
  SectorRow,
  SentimentPoint,
  StockSearchHit,
  Timeframe,
  TimelineItem,
  TradePoint,
  TradePresets,
  UsDisclosure,
  UsFinancial,
  UsQuote,
  UsScreenerQuery,
  UsScreenerResult,
} from "@/lib/types";

// 기본은 same-origin(빈 문자열) — 브라우저는 /api/... 를 현재 오리진으로 호출하고,
// Next.js rewrites(next.config.mjs)가 이를 loopback FastAPI 로 프록시한다. 그래야 프로덕션
// 빌드에 개발용 로컬호스트 주소가 구워지지 않는다. 별도 API 오리진을 쓸 때만 이 값을 설정.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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

// 수급 기반 섹터 로테이션(섹터 ETF). flow_score 내림차순.
export function fetchSectorFlow(market: FlowMarket): Promise<SectorFlowRow[]> {
  return getJson<SectorFlowRow[]>(`/api/sectors/flow?market=${market}`);
}

// 한 산업의 국내 섹터 ETF flow + 대응 미국 섹터 flow(선행). 섹터 상세 페이지용.
export function fetchSectorFlowDetail(industry: string): Promise<SectorFlowDetail> {
  return getJson<SectorFlowDetail>(
    `/api/sectors/flow/detail?industry=${encodeURIComponent(industry)}`,
  );
}

// 섹터 소속 종목 명단 + 시세. market=KR(judal 매칭) | US(대표종목 정적매핑).
export function fetchSectorStocks(
  industry: string,
  market: FlowMarket,
  opts?: { sort?: SectorStockSort; limit?: number; offset?: number },
): Promise<SectorStock[]> {
  const params = new URLSearchParams({ market });
  if (opts?.sort) {
    params.set("sort", opts.sort);
  }
  if (opts?.limit !== undefined) {
    params.set("limit", String(opts.limit));
  }
  if (opts?.offset !== undefined) {
    params.set("offset", String(opts.offset));
  }
  return getJson<SectorStock[]>(
    `/api/industries/${encodeURIComponent(industry)}/stocks?${params.toString()}`,
  );
}

// 섹터 상세 차트 구성(지수 4 + 국내/미국 섹터 ETF 심볼). 봉은 fetchChart 로 별도 조회.
export function fetchSectorChartMeta(industry: string): Promise<SectorChartMeta> {
  return getJson<SectorChartMeta>(
    `/api/sectors/${encodeURIComponent(industry)}/charts`,
  );
}

// 범용 봉 차트 — 지수·섹터 ETF·종목 공용. market=KR|US, tf=day|week|month.
export function fetchChart(
  symbol: string,
  market: FlowMarket,
  tf: ChartTimeframe,
): Promise<CandlePoint[]> {
  return getJson<CandlePoint[]>(
    `/api/chart?symbol=${encodeURIComponent(symbol)}&market=${market}&tf=${tf}`,
  );
}

export function fetchUsQuote(ticker: string): Promise<UsQuote> {
  return getJson<UsQuote>(`/api/us/companies/${encodeURIComponent(ticker)}/quote`);
}

export function fetchUsFinancials(ticker: string): Promise<UsFinancial> {
  return getJson<UsFinancial>(`/api/us/companies/${encodeURIComponent(ticker)}/financials`);
}

export function fetchUsScreener(query: UsScreenerQuery): Promise<UsScreenerResult> {
  const p = new URLSearchParams();
  if (query.mktcapMin !== undefined) p.set("mktcap_min", String(query.mktcapMin));
  if (query.mktcapMax !== undefined) p.set("mktcap_max", String(query.mktcapMax));
  if (query.liqMin !== undefined) p.set("liq_min", String(query.liqMin));
  if (query.perMax !== undefined) p.set("per_max", String(query.perMax));
  if (query.pbrMax !== undefined) p.set("pbr_max", String(query.pbrMax));
  if (query.momMin !== undefined) p.set("mom_min", String(query.momMin));
  if (query.exchange) p.set("exchange", query.exchange);
  if (query.sector) p.set("sector", query.sector);
  if (query.hasEvent) p.set("has_event", "true");
  p.set("sort", query.sort ?? "score");
  p.set("limit", String(query.limit ?? 50));
  p.set("offset", String(query.offset ?? 0));
  return getJson<UsScreenerResult>(`/api/us/screener?${p.toString()}`);
}

export function fetchUsDisclosures(ticker: string): Promise<UsDisclosure[]> {
  return getJson<UsDisclosure[]>(`/api/us/companies/${encodeURIComponent(ticker)}/disclosures`);
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
  strategy?: ScreenerStrategy;
  mktcapMax?: number;
  mktcapMin?: number;
  revYoyMin?: number;
  opGrowth?: ScreenerOpGrowth;
  momMin?: number;
  momMax?: number;
  liqMin?: number;
  // 가치 전략
  perMax?: number;
  pbrMax?: number;
  roeMin?: number;
  divMin?: number;
  market?: ScreenerMarket | "";
  sector?: string;
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
  set("strategy", query.strategy);
  set("mktcap_max", query.mktcapMax);
  set("mktcap_min", query.mktcapMin);
  set("rev_yoy_min", query.revYoyMin);
  set("op_growth", query.opGrowth);
  set("mom_min", query.momMin);
  set("mom_max", query.momMax);
  set("liq_min", query.liqMin);
  set("per_max", query.perMax);
  set("pbr_max", query.pbrMax);
  set("roe_min", query.roeMin);
  set("div_min", query.divMin);
  set("market", query.market);
  set("sector", query.sector);
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

// 섹터 필터용 섹터명 목록(국내 섹터 ETF 기준).
export function fetchScreenerSectors(): Promise<string[]> {
  return getJson<string[]>("/api/screener/sectors");
}

export function fetchCompanySummary(code: string): Promise<CompanySummary> {
  return getJson<CompanySummary>(`/api/companies/${encodeURIComponent(code)}/summary`);
}

export function fetchCompanyGrowth(code: string): Promise<CompanyGrowth> {
  return getJson<CompanyGrowth>(`/api/companies/${encodeURIComponent(code)}/growth`);
}

export function fetchCompanyAnalysis(code: string): Promise<CompanyAnalysis> {
  return getJson<CompanyAnalysis>(`/api/companies/${encodeURIComponent(code)}/analysis`);
}

export function fetchCandles(code: string, tf: Timeframe): Promise<CandlePoint[]> {
  return getJson<CandlePoint[]>(
    `/api/companies/${encodeURIComponent(code)}/candles?tf=${tf}`,
  );
}

export function fetchCompanyTrend(code: string): Promise<CompanyTrend> {
  return getJson<CompanyTrend>(`/api/companies/${encodeURIComponent(code)}/trend`);
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

// ── 보유종목(포트폴리오) ──────────────────────────────────────────────
export function fetchPortfolio(): Promise<PortfolioView> {
  return getJson<PortfolioView>("/api/portfolio");
}

export function fetchHoldings(): Promise<Holding[]> {
  return getJson<Holding[]>("/api/portfolio/holdings");
}

export async function saveHolding(code: string, body: HoldingInput): Promise<Holding> {
  const res = await fetch(apiUrl(`/api/portfolio/holdings/${code}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`보유종목 저장 실패 (${res.status})`);
  }
  return (await res.json()) as Holding;
}

export async function deleteHolding(code: string): Promise<void> {
  const res = await fetch(apiUrl(`/api/portfolio/holdings/${code}`), {
    method: "DELETE",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`보유종목 삭제 실패 (${res.status})`);
  }
}
