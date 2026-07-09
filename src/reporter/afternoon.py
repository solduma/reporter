"""오후 능동 리서치 — 오전 브리핑에서 키워드 추출 → 뉴스 검색 → 구조적 연결 분석."""

from __future__ import annotations

import logging
import re

import requests

from . import archive, news
from .config import Config
from .ollama_client import OllamaClient
from .shortener import UrlShortener
from .telegram import TelegramSender

logger = logging.getLogger(__name__)

_KEYWORD_SYSTEM = (
    "너는 오전 증권 브리핑에서 오후에 추적할 핵심 키워드를 뽑는다. "
    "종목명·테마·이슈 위주로 정확히 5개만, 한 줄에 하나씩, 번호 없이 출력한다."
)

_UPDATE_SYSTEM = (
    "너는 투자 자문위원이다. 오전 브리핑 내용과 오후 뉴스를 연결해 '오후에 뭐가 달라졌는지' 알려준다. "
    "핵심 규칙: 1차 정보(당연한 사실)는 쓰지 않는다. "
    "'유가 올랐다'가 아니라 '유가 급등 → 국내 물류비 → 특정 종목 마진 영향'처럼 "
    "2차·3차 구조적 연결만 전달한다. 300자 이내. 근거 없으면 아는 척하지 않는다."
)

_UPDATE_TEMPLATE = """[오전 브리핑 요약]
{briefing}

[키워드 '{keyword}' 관련 오후 뉴스]
{articles}

위 키워드에 대해 오전 대비 오후 변화를 300자 이내로 분석해라. 구조적 연결만."""


def _extract_keywords(client: OllamaClient, model: str, briefing: str) -> list[str]:
    raw = client.chat(model, _KEYWORD_SYSTEM, briefing[:6000])
    keywords = []
    for line in raw.splitlines():
        # 목록 마커(1. / 1) / - / • / *)만 제거. '2차전지' '5G' 처럼 숫자로 시작하는 키워드는 보존.
        cleaned = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", line).strip()
        if cleaned:
            keywords.append(cleaned)
    return keywords[:5]


def run_afternoon_research(config: Config) -> int:
    """오전 브리핑 로그를 읽어 키워드별 오후 업데이트를 발송한다. 발송한 메시지 수를 반환한다."""
    log_path = config.logs_dir / "today_briefing.txt"
    if not log_path.exists():
        logger.info("no morning briefing log; skipping afternoon research")
        return 0
    briefing = log_path.read_text(encoding="utf-8")

    client = OllamaClient(config.ollama_host, config.ollama_api_key)
    keywords = _extract_keywords(client, config.insight_model, briefing)
    logger.info("afternoon keywords: %s", keywords)

    sender = TelegramSender(config.telegram_bot_token, config.telegram_chat_id)
    session = requests.Session()
    shortener = UrlShortener(config.logs_dir / "url_cache.json", session=session)
    sent = 0
    for keyword in keywords:
        articles = news.search(keyword, limit=5, session=session)
        if not articles:
            continue
        article_lines = "\n".join(f"- {a.title} ({a.source})" for a in articles)
        prompt = _UPDATE_TEMPLATE.format(
            briefing=briefing[:2000], keyword=keyword, articles=article_lines
        )
        try:
            analysis = client.chat(config.insight_model, _UPDATE_SYSTEM, prompt, temperature=0.5)
        except Exception as e:
            logger.warning("afternoon analysis failed for %s: %s", keyword, e)
            continue

        # 출처 이름만이 아니라 실제 기사 페이지 링크를 함께 붙인다(상위 3건).
        source_lines = "\n".join(
            f"• {a.title} ({a.source})\n{shortener.shorten(a.link)}"
            for a in articles[:3]
            if a.link
        )
        message = f"**📌 {keyword} 업데이트**\n{analysis}\n\n📰 관련 기사\n{source_lines}"
        sender.send(message)
        archive.record(
            config,
            "afternoon",
            title=f"📌 {keyword} 업데이트",
            body=message,
            source_refs={
                "keywords": [keyword],
                "news": [{"title": a.title, "url": a.link, "source": a.source} for a in articles[:3]],
            },
        )
        sent += 1

    logger.info("afternoon research sent %d updates", sent)
    return sent
