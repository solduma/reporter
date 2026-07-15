"""Google News RSS 실시간 검색."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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
    published_at: datetime | None = None  # RSS pubDate(tz-aware). 파싱 실패/부재 시 None.


# 장중 시장 뉴스 검색 키워드(국내 장 실시간). pipeline 의 장중뉴스와 동일 키워드를 공유한다.
MARKET_NEWS_KEYWORDS = ["코스피", "코스닥", "증시", "환율", "금리"]
# 간밤 미국/글로벌 뉴스 검색 키워드(개장 전 브리핑용).
GLOBAL_NEWS_KEYWORDS = ["미국 증시", "나스닥", "연준", "뉴욕증시", "글로벌 경제"]


def collect(
    keywords: list[str],
    limit: int,
    session: requests.Session | None = None,
    max_age_hours: float | None = None,
) -> list[NewsItem]:
    """여러 키워드로 뉴스를 모아 제목 중복 제거 후, **최신순**으로 상위 limit 건을 반환한다.

    max_age_hours 지정 시 그보다 오래된 기사는 제외한다(장중 시황이 장 초 뉴스에 고정되지 않게).
    published_at 이 있는 기사를 최신순 우선 정렬하고, 시각 미상 기사는 뒤로 보낸다(수집 순서 유지).
    pipeline._collect_market_news 를 공용화한 것. api 시황 파이프라인과 CLI 가 함께 쓴다.
    """
    session = session or requests.Session()
    cutoff = (
        datetime.now(UTC) - timedelta(hours=max_age_hours)
        if max_age_hours is not None
        else None
    )
    seen: set[str] = set()
    collected: list[NewsItem] = []
    for kw in keywords:
        for item in search(kw, limit=5, session=session):
            if not item.title or item.title in seen:
                continue
            if cutoff is not None and item.published_at is not None and item.published_at < cutoff:
                continue  # 너무 오래된 기사 제외(시각 미상은 통과 — 배제 정보 부족)
            seen.add(item.title)
            collected.append(item)
    # 최신순 정렬(시각 미상은 맨 뒤). datetime.min 은 tz-aware 로 맞춰 비교 오류 방지.
    _oldest = datetime.min.replace(tzinfo=UTC)
    collected.sort(key=lambda it: it.published_at or _oldest, reverse=True)
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
            items.append(
                NewsItem(title=title, source=source, link=link, published_at=_parse_pubdate(item))
            )
    return items


def _parse_pubdate(item: ElementTree.Element) -> datetime | None:
    """RSS <pubDate>(RFC822)를 tz-aware datetime 으로. 없거나 파싱 실패 시 None."""
    raw = (item.findtext("pubDate") or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:  # naive → UTC 로 간주
        dt = dt.replace(tzinfo=UTC)
    return dt
