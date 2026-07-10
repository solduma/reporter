"""Google News RSS 실시간 검색."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}


@dataclass
class NewsItem:
    title: str
    source: str
    link: str


# 장중 시장 뉴스 검색 키워드(국내 장 실시간). pipeline 의 장중뉴스와 동일 키워드를 공유한다.
MARKET_NEWS_KEYWORDS = ["코스피", "코스닥", "증시", "환율", "금리"]
# 간밤 미국/글로벌 뉴스 검색 키워드(개장 전 브리핑용).
GLOBAL_NEWS_KEYWORDS = ["미국 증시", "나스닥", "연준", "뉴욕증시", "글로벌 경제"]


def collect(
    keywords: list[str], limit: int, session: requests.Session | None = None
) -> list[NewsItem]:
    """여러 키워드로 뉴스를 모아 제목 중복을 제거하고 상위 limit 건을 반환한다.

    pipeline._collect_market_news 를 공용화한 것. api 시황 파이프라인과 CLI 가 함께 쓴다.
    """
    session = session or requests.Session()
    seen: set[str] = set()
    collected: list[NewsItem] = []
    for kw in keywords:
        for item in search(kw, limit=5, session=session):
            if item.title and item.title not in seen:
                seen.add(item.title)
                collected.append(item)
    return collected[:limit]


def search(keyword: str, limit: int = 5, session: requests.Session | None = None) -> list[NewsItem]:
    session = session or requests.Session()
    url = _RSS.format(q=quote(keyword))
    try:
        resp = session.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("news search failed for %s: %s", keyword, e)
        return []

    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as e:
        logger.warning("news RSS parse failed for %s: %s", keyword, e)
        return []

    items: list[NewsItem] = []
    for item in list(root.iterfind(".//item"))[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if title:
            items.append(NewsItem(title=title, source=source, link=link))
    return items
