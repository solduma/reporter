import type {
  CandlePoint,
  CompanySummary,
  FinancialPeriod,
  Industry,
  MarketBrief,
  Peer,
  Report,
  ReportCategory,
  SentimentPoint,
  Timeframe,
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

export function fetchReports(category: ReportCategory): Promise<Report[]> {
  return getJson<Report[]>(`/api/today/reports?category=${category}`);
}

export function reportPdfUrl(id: number): string {
  return apiUrl(`/api/reports/${id}/pdf`);
}

export function fetchIndustries(): Promise<Industry[]> {
  return getJson<Industry[]>("/api/industries");
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

export function fetchCompanySummary(code: string): Promise<CompanySummary> {
  return getJson<CompanySummary>(`/api/companies/${encodeURIComponent(code)}/summary`);
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
