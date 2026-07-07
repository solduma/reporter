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
