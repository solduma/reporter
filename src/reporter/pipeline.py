"""오전 브리핑 파이프라인 — 수집 → 선별 → PDF 추출 → 2단계 분석 → 텔레그램 발송."""

from __future__ import annotations

import logging
from datetime import datetime

from . import analyzer, archive, article, fallback, market, news, us_market
from .config import Config
from .crawler import crawl_categories
from .forum import ForumPublisher
from .grouping import group_by_entity
from .models import CATEGORY_NAMES, Briefing, DigestResult, Report
from .ollama_client import OllamaClient
from .pdf import enrich_with_text
from .selector import select_top
from .shortener import UrlShortener
from .telegram import TelegramError, TelegramSender

# 뉴스 종합 시 본문까지 크롤할 상위 기사 수(headless 라 무거워 소수만).
# 매시 실행(--news)이라 런타임을 bound 하려 3건으로 제한. 나머지는 제목만 참고.
_ARTICLE_CRAWL_TOP = 3

logger = logging.getLogger(__name__)

_DIVIDER = "─" * 20

# 카테고리별 종합 digest 헤더 이모지
_DIGEST_HEADER = {
    "market_info": "📈 시황 종합",
    "invest": "💡 투자 종합",
    "economy": "🌍 경제 종합",
    "debenture": "💵 채권 종합",
}
# digest 카테고리 → 아카이브 kind (broadcast.kind 와 일치)
_DIGEST_KIND = {
    "market_info": "digest_market",
    "invest": "digest_invest",
    "economy": "digest_econ",
    "debenture": "digest_bond",
}
# 장중 시장 뉴스 검색 키워드
_MARKET_NEWS_KEYWORDS = ["코스피", "코스닥", "증시", "환율", "금리"]
# 간밤 미국/글로벌 뉴스 검색 키워드
_GLOBAL_NEWS_KEYWORDS = ["미국 증시", "나스닥", "연준", "뉴욕증시", "글로벌 경제"]


def _format_message(briefing: Briefing) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    cats = ", ".join(CATEGORY_NAMES.get(c, c) for c in briefing.categories)
    header = f"**📈 데일리 증권 리포트 브리핑 — {date}**\n(리포트 {briefing.report_count}건 · {cats})\n{'─' * 20}\n"
    return header + briefing.text


def _format_report_message(report: Report) -> str:
    cat = CATEGORY_NAMES.get(report.category, report.category)
    who = f"{report.stock_name} · {report.broker}" if report.stock_name else report.broker
    lines = [f"**📄 [{cat}] {report.title}**", f"🏦 {who}", "", report.summary]
    # 읽기페이지(read_url)는 모바일에서 목록으로 튀므로 PDF 원본을 우선 링크한다.
    link = report.pdf_url or report.read_url
    if link:
        lines += ["", f"🔗 {link}"]
    return "\n".join(lines)


def _report_link(report: Report) -> str:
    return report.pdf_url or report.read_url or ""


def _format_entity_message(
    entity: str, category: str, summary: str, reports: list[Report], shortener: UrlShortener
) -> str:
    """종목/산업 단위 종합 + 그 단위 모든 리포트 링크(단축)."""
    header = "🏢 종목 브리핑" if category == "company" else "🏭 산업 브리핑"
    lines = [f"**{header} — {entity}**", f"(리포트 {len(reports)}건 종합)", _DIVIDER, summary, "", "🔗 리포트 원문"]
    for r in reports:
        link = _report_link(r)
        if link:
            lines.append(f"• [{r.broker}] {r.title}\n{shortener.shorten(link)}")
    return "\n".join(lines)


def _format_digest_message(
    digest: DigestResult, shortener: UrlShortener, closing: bool = False
) -> str:
    """카테고리 장문 종합 + 인용 상위 5개 소스 링크(단축)."""
    date = datetime.now().strftime("%Y-%m-%d")
    header = "🔔 마감 시황 종합" if closing else _DIGEST_HEADER.get(digest.category, "📊 종합")
    lines = [f"**{header} — {date}**", f"(리포트 {digest.report_count}건 참고)", _DIVIDER, digest.text]
    if digest.sources:
        lines += ["", "📚 핵심 근거 리포트"]
        for i, r in enumerate(digest.sources, 1):
            link = _report_link(r)
            short = shortener.shorten(link) if link else ""
            lines.append(f"{i}. [{r.broker}] {r.title}\n{short}" if short else f"{i}. [{r.broker}] {r.title}")
    return "\n".join(lines)


def _collect_market_news(keywords: list[str], limit: int, session) -> list[news.NewsItem]:
    """여러 키워드로 뉴스를 모아 제목 중복을 제거하고 상위 limit 건을 반환한다."""
    seen: set[str] = set()
    collected: list[news.NewsItem] = []
    for kw in keywords:
        for item in news.search(kw, limit=5, session=session):
            if item.title and item.title not in seen:
                seen.add(item.title)
                collected.append(item)
    return collected[:limit]


def _shortener(config: Config, session) -> UrlShortener:
    return UrlShortener(config.logs_dir / "url_cache.json", session=session)


def _synthesize_news(client: OllamaClient, model: str, items: list[news.NewsItem]) -> str:
    """상위 기사 본문(headless)+나머지 제목으로 서술형 시장 요약을 만든다."""
    blocks: list[str] = []
    for it in items[:_ARTICLE_CRAWL_TOP]:
        body = article.fetch_article_text(it.link)
        blocks.append(f"[{it.source}] {it.title}\n{body}" if body else f"[{it.source}] {it.title}")
    for it in items[_ARTICLE_CRAWL_TOP:]:
        blocks.append(f"[{it.source}] {it.title}")
    return analyzer.synthesize_news(client, model, blocks)


def _format_news_digest(
    header: str, summary: str, items: list[news.NewsItem], shortener: UrlShortener
) -> str:
    date = datetime.now().strftime("%m-%d %H:%M")
    lines = [f"**{header} — {date}**", _DIVIDER]
    if summary:
        lines += [summary, "", "🔗 관련 기사"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.title} ({it.source})\n{shortener.shorten(it.link)}")
    return "\n".join(lines)


def run_morning_briefing(config: Config, categories: list[str], top_n: int = 5) -> str | None:
    """오전 브리핑 실행. 발송한 브리핑 텍스트를 반환하고, 리포트가 없으면 None."""
    logger.info("collecting categories: %s", categories)
    reports = crawl_categories(categories)
    if not reports:
        logger.info("no reports today for %s", categories)
        return None

    selected = select_top(reports, top_n=top_n)
    logger.info("selected %d reports", len(selected))

    enriched = enrich_with_text(selected)
    if not enriched:
        logger.info("no PDF text extracted; nothing to analyze")
        return None

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    summarized = analyzer.summarize_reports(client, config.summary_model, enriched)
    if not summarized:
        logger.info("no summaries produced")
        return None

    briefing = analyzer.synthesize_insight(client, config.insight_model, summarized)
    message = _format_message(briefing)

    TelegramSender(config.telegram_bot_token, config.telegram_chat_id).send(message)
    archive.record(config, "morning", title="📈 데일리 증권 리포트 브리핑", body=message)

    # 오후 리서치가 참조할 수 있도록 당일 브리핑 로그 저장
    log_path = config.logs_dir / "today_briefing.txt"
    log_path.write_text(message, encoding="utf-8")
    logger.info("briefing sent and logged to %s", log_path)
    return message


def run_per_report_briefing(
    config: Config, categories: list[str], target_date: str | None = None
) -> int:
    """선별·종합 없이 해당 날짜 발행 리포트를 전량 요약해 리포트당 1건씩 발송한다.

    발송한 리포트 수를 반환한다. 조회수 순으로 정렬해 중요한 것부터 보낸다.
    """
    reports = crawl_categories(categories, target_date=target_date)
    if not reports:
        logger.info("no reports for %s on %s", categories, target_date or "today")
        return 0

    enriched = enrich_with_text(reports)
    if not enriched:
        logger.info("no PDF text extracted; nothing to send")
        return 0

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    summarized = analyzer.summarize_reports(client, config.summary_model, enriched)
    summarized.sort(key=lambda r: r.views, reverse=True)

    sender = TelegramSender(config.telegram_bot_token, config.telegram_chat_id)
    for report in summarized:
        sender.send(_format_report_message(report))

    logger.info("per-report briefing sent %d reports", len(summarized))
    return len(summarized)


def run_per_entity_briefing(config: Config, categories: list[str], target_date: str | None = None) -> int:
    """종목·산업을 개별 종목/산업 단위로 종합해 발송한다(단위별 모든 링크 포함).

    발송한 메시지(종목/산업) 수를 반환한다.
    """
    reports = crawl_categories(categories, target_date=target_date)
    if not reports:
        logger.info("no reports for %s", categories)
        return 0
    enriched = enrich_with_text(reports)
    if not enriched:
        return 0

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    summarized = analyzer.summarize_reports(client, config.summary_model, enriched)
    if not summarized:
        return 0

    import requests

    sender = TelegramSender(config.telegram_bot_token, config.telegram_chat_id)
    shortener = _shortener(config, requests.Session())
    groups = group_by_entity(summarized)

    # 토픽 발송 시 category(company/industry)별로 메시지를 모아 각 일자별 토픽에 누적한다.
    by_kind: dict[str, list[str]] = {}
    sent = 0
    for entity, group in groups.items():
        group.sort(key=lambda r: r.views, reverse=True)
        category = group[0].category
        summary = analyzer.synthesize_entity(client, config.insight_model, group)
        message = _format_entity_message(entity, category, summary, group, shortener)
        header = "🏢 종목 브리핑" if category == "company" else "🏭 산업 브리핑"
        if config.use_topics:
            by_kind.setdefault(category, []).append(message)
        else:
            sender.send(message)
        archive.record_entity(config, entity, category, f"{header} — {entity}", message, group)
        sent += 1

    if config.use_topics:
        publisher = ForumPublisher(config, sender)
        for kind, entries in by_kind.items():
            try:
                publisher.publish(kind, entries)
            except TelegramError as e:
                # 포럼 아님/권한 없음 → plain 발송으로 폴백(유실 방지)
                fallback.log_fallback(
                    "forum.topic_to_plain.entity",
                    reason=f"포럼 토픽 발송 실패 → plain 폴백 ({e})",
                    detail=kind,
                )
                for body in entries:
                    sender.send(body)

    logger.info("per-entity briefing sent %d messages", sent)
    return sent


def run_category_digest(
    config: Config, category: str, closing: bool = False, target_date: str | None = None
) -> str | None:
    """한 카테고리를 장문 종합 1건으로 발송한다(인용 상위 5개 링크 포함)."""
    reports = crawl_categories([category], target_date=target_date)
    if not reports:
        logger.info("no reports for %s", category)
        return None

    # 시황은 오전/마감을 분리한다: 오전 종합은 국내 마감시황(전일 리뷰)을 제외하고,
    # 17시 마감(closing)은 국내 마감시황만 모은다. (미국 마감시황은 오전에 유지.)
    if category == "market_info":
        morning, domestic_closing = market.split_by_closing(reports)
        reports = domestic_closing if closing else morning
        if not reports:
            logger.info("no %s reports after closing filter", "closing" if closing else "morning")
            return None

    enriched = enrich_with_text(reports)
    if not enriched:
        return None

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    summarized = analyzer.summarize_reports(client, config.summary_model, enriched)
    if not summarized:
        return None

    import requests

    digest = analyzer.synthesize_digest(client, config.insight_model, summarized)
    shortener = _shortener(config, requests.Session())
    message = _format_digest_message(digest, shortener, closing=closing)
    TelegramSender(config.telegram_bot_token, config.telegram_chat_id).send(message)

    kind = "closing" if closing else _DIGEST_KIND.get(category, "digest_market")
    header = "🔔 마감 시황 종합" if closing else _DIGEST_HEADER.get(category, "📊 종합")
    archive.record_digest(config, kind, header, message, digest.sources)
    logger.info("%s digest sent (closing=%s)", category, closing)
    return message


def run_market_news(config: Config) -> int:
    """장중 시장 뉴스를 종합·요약해 발송한다(제목 나열 아님). 발송 청크 수 반환."""
    import requests

    session = requests.Session()
    items = _collect_market_news(_MARKET_NEWS_KEYWORDS, limit=8, session=session)
    if not items:
        logger.info("no market news")
        return 0

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    summary = _synthesize_news(client, config.insight_model, items)
    shortener = _shortener(config, session)
    top = items[:5]
    message = _format_news_digest("📰 장중 시장 뉴스", summary, top, shortener)

    sender = TelegramSender(config.telegram_bot_token, config.telegram_chat_id)
    if config.use_topics:
        # 장중 뉴스는 매시 발송 → 하나의 일자별 토픽에 누적(다른 메시지에 묻히지 않게).
        try:
            sent = ForumPublisher(config, sender).publish("market_news", [message])
        except TelegramError as e:
            fallback.log_fallback(
                "forum.topic_to_plain.news",
                reason=f"장중뉴스 토픽 발송 실패 → plain 폴백 ({e})",
            )
            sent = sender.send(message)
    else:
        sent = sender.send(message)

    archive.record(
        config,
        "market_news",
        title="📰 장중 시장 뉴스",
        body=message,
        source_refs={"news": [{"title": it.title, "url": it.link, "source": it.source} for it in top]},
    )
    return sent


def run_premarket(config: Config) -> int:
    """아침 미국증시 마감(지수 수치) + 간밤 주요 뉴스 종합 발송."""
    import requests

    session = requests.Session()
    date = datetime.now().strftime("%Y-%m-%d")
    lines = [f"🌅 굿모닝 미국증시 — {date}", _DIVIDER, "📊 간밤 미국 증시"]
    for q in us_market.fetch_us_indices(session):
        arrow = "▲" if q.rising else "▼" if q.rising is False else "-"
        lines.append(f"• {q.name}  {q.close}  {arrow} {q.change} ({q.change_ratio}%)")

    items = _collect_market_news(_GLOBAL_NEWS_KEYWORDS, limit=10, session=session)
    if items:
        client = OllamaClient(config.ollama_host, config.ollama_api_key)
        summary = _synthesize_news(client, config.insight_model, items)
        if summary:
            lines += ["", "📝 간밤 시장 요약", summary]
        shortener = _shortener(config, session)
        lines += ["", "🗞 주요 뉴스"]
        for i, it in enumerate(items[:10], 1):
            lines.append(f"{i}. {it.title} ({it.source})\n{shortener.shorten(it.link)}")

    message = "\n".join(lines)
    sent = TelegramSender(config.telegram_bot_token, config.telegram_chat_id).send(message)
    archive.record(
        config,
        "premarket",
        title="🌅 굿모닝 미국증시",
        body=message,
        source_refs={"news": [{"title": it.title, "url": it.link, "source": it.source} for it in items[:10]]},
    )
    return sent
