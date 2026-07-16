"""딥다이브 웹 리서치 — 네이버 검색 API 로 후보를 찾고 블로그 본문을 크롤해 합친다.

한국 종목은 네이버 블로그에 심층 리서치 글이 많아 핵심 소스. 검색(제목·요약·링크) → 블로그 본문
크롤(.se-main-container) → 텍스트 묶음 반환. 네이버 키 미설정 시 빈 결과(graceful degrade).
"""

from __future__ import annotations

import logging

import requests

from app.adapters.external import article_crawler, blog_crawler, naver_search
from app.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_CRAWL = 4  # 블로그 본문 크롤 상한(토큰·시간 통제)
_DEFAULT_NEWS_CRAWL = 3  # 뉴스 본문 크롤 상한(수주·계약·소송 등 실질 정보원)


def research(
    settings: Settings,
    query: str,
    session: requests.Session,
    *,
    max_results: int = 15,
    crawl_bodies: int = _DEFAULT_CRAWL,
    news_display: int = 10,
    crawl_news: int = _DEFAULT_NEWS_CRAWL,
    sort: str = "sim",
) -> dict:
    """웹 리서치. 반환: {query, hits:[...], bodies:[{url,title,body,source}]}.

    hits 는 블로그+뉴스 검색 요약, bodies 는 상위 블로그·뉴스 본문(크롤 성공분). 뉴스 본문까지 읽어
    수주·계약·소송 등 이벤트 실체를 확보한다(제목·스니펫만으로는 놓침). 키 없으면 빈 구조.
    """
    cid, secret = settings.naver_client_id, settings.naver_client_secret
    if not cid or not secret:
        return {"query": query, "hits": [], "bodies": [], "note": "네이버 검색 키 미설정"}

    # max_results 0 이면 블로그 검색 생략(뉴스 전용 호출 — event_search). display=0 은 API 400.
    blog_hits = (
        naver_search.search_blog(cid, secret, query, session, display=max_results, sort=sort)
        if max_results > 0 else []
    )
    # 뉴스는 최신순(수주·계약은 시의성). display 를 늘려 이벤트 기사 포착률을 높인다.
    news_hits = naver_search.search_news(cid, secret, query, session, display=news_display, sort="date")
    hits = blog_hits + news_hits

    bodies: list[dict] = []
    # 블로그 본문(심층 리서치 글). 네이버 블로그 링크만.
    for h in blog_hits:
        if len(bodies) >= crawl_bodies:
            break
        body = blog_crawler.crawl_blog(h.link, session)
        if body and body.get("body"):
            bodies.append({**body, "source": "blog"})
    # 뉴스 본문(수주·계약·소송 등 이벤트 실체). 범용 기사 추출기.
    news_bodies = 0
    for h in news_hits:
        if news_bodies >= crawl_news:
            break
        body = article_crawler.crawl_article(h.link, session)
        if body and body.get("body"):
            bodies.append({**body, "source": "news"})
            news_bodies += 1

    return {
        "query": query,
        "hits": [
            {"title": h.title, "link": h.link, "description": h.description,
             "source": h.source, "post_date": h.post_date}
            for h in hits
        ],
        "bodies": bodies,
    }
