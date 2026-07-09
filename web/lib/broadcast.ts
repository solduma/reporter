import type { BroadcastKind } from "@/lib/types";

// 브로드캐스트 종류 → 표시 라벨(이모지 포함). 텔레그램 헤더와 의미를 맞춘다.
export const BROADCAST_KIND_LABEL: Record<BroadcastKind, string> = {
  digest_market: "📈 시황 종합",
  digest_invest: "💡 투자 종합",
  digest_econ: "🌍 경제 종합",
  digest_bond: "💵 채권 종합",
  closing: "🔔 마감 시황",
  market_news: "📰 장중 뉴스",
  premarket: "🌅 미국증시",
  afternoon: "📌 오후 리서치",
  morning: "☕ 오전 브리핑",
  per_entity: "🏢 종목·산업 브리핑",
};

export function broadcastKindLabel(kind: string): string {
  return BROADCAST_KIND_LABEL[kind as BroadcastKind] ?? kind;
}
