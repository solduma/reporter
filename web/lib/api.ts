import type { MarketBrief, Report, ReportCategory } from "@/lib/types";

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
