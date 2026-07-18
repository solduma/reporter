"""네이버 검색 API — 딥다이브 웹 리서치용(블로그·뉴스). 일 25,000회 무료, 헤더 인증 2개.

한국 종목 리서치는 네이버 블로그에 개인 애널리스트·투자자의 심층 글이 많아 핵심 소스다. 검색 API 로
후보(제목·요약·링크·작성일)를 얻고, blog_crawler 가 본문을 크롤한다. 키 미설정 시 빈 결과(graceful).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"
_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")  # 검색결과 title/description 의 <b> 하이라이트 제거


@dataclass
class SearchHit:
    title: str
    link: str
    description: str
    source: str  # blog | news
    post_date: str  # YYYYMMDD (없으면 "")
    blogger: str = ""  # 블로그명(블로그만)


def _strip(text: str) -> str:
    """HTML 태그·엔티티(&amp; 등) 제거. 검색결과 문자열 정제."""
    from html import unescape

    return _TAG_RE.sub("", unescape(text or "")).strip()


def _norm_date(raw: str) -> str:
    """블로그 postdate(YYYYMMDD) 또는 뉴스 pubDate(RFC822)를 YYYYMMDD 로 통일. 실패 시 ""(정렬 후순위).

    뉴스 pubDate 예: 'Mon, 26 May 2026 09:24:00 +0900'. 파싱해 날짜 비교·recency 정렬에 쓴다.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.isdigit() and len(raw) == 8:  # 블로그 postdate
        return raw
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return ""


def _search(url: str, source: str, client_id: str, client_secret: str, query: str,
            display: int, sort: str, session: requests.Session) -> list[SearchHit]:
    if not client_id or not client_secret:
        return []
    try:
        resp = session.get(
            url,
            params={"query": query, "display": min(display, 100), "sort": sort},
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("naver search failed (%s) %s: %s", source, query, e)
        return []
    hits: list[SearchHit] = []
    for it in items:
        hits.append(SearchHit(
            title=_strip(it.get("title", "")),
            link=(it.get("link") or "").strip(),
            description=_strip(it.get("description", "")),
            source=source,
            post_date=_norm_date(it.get("postdate") or it.get("pubDate") or ""),
            blogger=_strip(it.get("bloggername", "")),
        ))
    return hits


def search_blog(client_id: str, client_secret: str, query: str, session: requests.Session,
                display: int = 20, sort: str = "sim") -> list[SearchHit]:
    """블로그 검색. sort=sim(정확도)|date(최신). query 는 requests 가 UTF-8 인코딩."""
    return _search(_BLOG_URL, "blog", client_id, client_secret, query, display, sort, session)


def search_news(client_id: str, client_secret: str, query: str, session: requests.Session,
                display: int = 10, sort: str = "sim") -> list[SearchHit]:
    """뉴스 검색(보강용)."""
    return _search(_NEWS_URL, "news", client_id, client_secret, query, display, sort, session)
