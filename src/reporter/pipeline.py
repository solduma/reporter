"""오전 브리핑 파이프라인 — 수집 → 선별 → PDF 추출 → 2단계 분석 → 텔레그램 발송."""

from __future__ import annotations

import logging
from datetime import datetime

from . import analyzer
from .config import Config
from .crawler import crawl_categories
from .models import CATEGORY_NAMES, Briefing
from .ollama_client import OllamaClient
from .pdf import enrich_with_text
from .selector import select_top
from .telegram import TelegramSender

logger = logging.getLogger(__name__)


def _format_message(briefing: Briefing) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    cats = ", ".join(CATEGORY_NAMES.get(c, c) for c in briefing.categories)
    header = f"📈 데일리 증권 리포트 브리핑 — {date}\n(리포트 {briefing.report_count}건 · {cats})\n{'─' * 20}\n"
    return header + briefing.text


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

    # 오후 리서치가 참조할 수 있도록 당일 브리핑 로그 저장
    log_path = config.logs_dir / "today_briefing.txt"
    log_path.write_text(message, encoding="utf-8")
    logger.info("briefing sent and logged to %s", log_path)
    return message
