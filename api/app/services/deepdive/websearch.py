"""딥다이브 웹 리서치 — 네이버 검색 API 로 후보를 찾고 블로그 본문을 크롤해 합친다.

한국 종목은 네이버 블로그에 심층 리서치 글이 많아 핵심 소스. 검색(제목·요약·링크) → 블로그 본문
크롤(.se-main-container) → 텍스트 묶음 반환. 네이버 키 미설정 시 빈 결과(graceful degrade).
"""

from __future__ import annotations

import logging

import requests

from app.adapters.external import blog_crawler, naver_search
from app.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_CRAWL = 4  # 블로그 본문 크롤 상한(토큰·시간 통제)


def research(
    settings: Settings,
    query: str,
    session: requests.Session,
    *,
    max_results: int = 15,
    crawl_bodies: int = _DEFAULT_CRAWL,
    sort: str = "sim",
) -> dict:
    """웹 리서치. 반환: {query, hits:[{title,link,description,source,post_date}], bodies:[{url,title,body}]}.

    hits 는 블로그+뉴스 검색 요약, bodies 는 상위 블로그 글 본문(크롤 성공분). 키 없으면 빈 구조.
    """
    cid, secret = settings.naver_client_id, settings.naver_client_secret
    if not cid or not secret:
        return {"query": query, "hits": [], "bodies": [], "note": "네이버 검색 키 미설정"}

    blog_hits = naver_search.search_blog(cid, secret, query, session, display=max_results, sort=sort)
    news_hits = naver_search.search_news(cid, secret, query, session, display=5, sort="date")
    hits = blog_hits + news_hits

    # 상위 블로그 글 본문 크롤(네이버 블로그 링크만). 매너 sleep 은 세션 재사용+소량이라 생략.
    bodies: list[dict] = []
    for h in blog_hits:
        if len(bodies) >= crawl_bodies:
            break
        body = blog_crawler.crawl_blog(h.link, session)
        if body and body.get("body"):
            bodies.append(body)

    return {
        "query": query,
        "hits": [
            {"title": h.title, "link": h.link, "description": h.description,
             "source": h.source, "post_date": h.post_date}
            for h in hits
        ],
        "bodies": bodies,
    }
