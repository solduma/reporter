"""딥다이브 웹 리서치 — 네이버 검색 API 로 후보를 찾고 블로그 본문을 크롤해 합친다.

한국 종목은 네이버 블로그에 심층 리서치 글이 많아 핵심 소스. 검색(제목·요약·링크) → 블로그 본문
크롤(.se-main-container) → 텍스트 묶음 반환. 네이버 키 미설정 시 빈 결과(graceful degrade).
"""

from __future__ import annotations

import logging
import re

import requests

from app.adapters.external import article_crawler, blog_crawler, naver_search
from app.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_CRAWL = 4  # 블로그 본문 크롤 상한(토큰·시간 통제)
_DEFAULT_NEWS_CRAWL = 3  # 뉴스 본문 크롤 상한(수주·계약·소송 등 실질 정보원)

_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^\w가-힣]+")


def _norm_title(title: str) -> str:
    """제목 정규화(dedup 클러스터 키) — 소문자·공백/기호 제거. 같은 사건 다른 표기를 합친다."""
    return _NONWORD_RE.sub("", _WS_RE.sub("", (title or "").lower()))


def _relevance(hit_title: str, hit_desc: str, aliases: list[str]) -> int:
    """제목·스니펫에 종목/관계사 alias 가 몇 개 유형 나타나는지(0=무관). 관련성 1차 판정.

    제목에 있으면 확실, 스니펫만 있어도 관련(본문에만 있는 케이스는 호출측이 본문 fetch 로 보강).
    """
    hay = f"{hit_title} {hit_desc}"
    hay_nospace = _WS_RE.sub("", hay)
    hits = 0
    for a in aliases:
        if not a:
            continue
        if a in hay or _WS_RE.sub("", a) in hay_nospace:
            hits += 1
    return hits


def _rerank_dedup(
    hits: list[naver_search.SearchHit],
    aliases: list[str],
    seen_titles: set[str],
    recency_weight: float,
) -> list[naver_search.SearchHit]:
    """관련성 필터 + 제목/URL dedup + (정확도 순위, recency) 결합 재랭킹.

    - aliases 매칭 0 이고 제목·스니펫에 근거 없는 hit 은 제외(단 본문 확인 대상은 호출측이 판단).
    - 이미 본 제목(seen_titles)·같은 정규화 제목 클러스터는 대표 1건만.
    - 결합점수 = (1-w)·(정확도 순위 역수) + w·(recency). 목적별 w(recency_weight)로 trade-off 조절.
    입력 hits 는 원 정렬(정확도 또는 최신)을 보존한 순서라고 가정하고 index 를 순위 프록시로 쓴다.
    """
    n = len(hits) or 1
    dates = sorted({h.post_date for h in hits if h.post_date})
    date_rank = {d: i for i, d in enumerate(dates)}  # 오래된→최신 인덱스
    scored: list[tuple[float, naver_search.SearchHit]] = []
    local_seen: set[str] = set()
    for idx, h in enumerate(hits):
        rel = _relevance(h.title, h.description, aliases)
        if rel == 0:
            continue  # 관련 근거 없음(본문 확인은 호출측 별도 경로)
        key = _norm_title(h.title)
        if not key or key in seen_titles or key in local_seen:
            continue  # 이미 본/중복 제목 — 대표 1건만
        local_seen.add(key)
        sim_score = 1.0 - idx / n  # 원 정렬 상위일수록 ↑
        rec_score = (date_rank.get(h.post_date, 0) / (len(dates) - 1)) if len(dates) > 1 else 0.5
        score = (1 - recency_weight) * sim_score + recency_weight * rec_score + 0.1 * (rel - 1)
        scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    for _, h in scored:
        seen_titles.add(_norm_title(h.title))  # job 스코프 seen 갱신(단계 간 중복 방지)
    return [h for _, h in scored]


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
    aliases: list[str] | None = None,
    seen_titles: set[str] | None = None,
    recency_weight: float = 0.4,
) -> dict:
    """웹 리서치. 반환: {query, hits:[...], bodies:[{url,title,body,source}]}.

    hits 는 관련성 필터·제목 dedup·재랭킹을 거친 블로그+뉴스 요약. bodies 는 대표 hit 본문(크롤).
    aliases: 관련성 판정용 종목·관계사명(없으면 query 기반). seen_titles: job 스코프 이미 본 제목
    (단계 간 중복 방지, 호출측이 유지). recency_weight: recency↔정확도 trade-off(0=정확도, 1=최신).
    키 없으면 빈 구조.
    """
    cid, secret = settings.naver_client_id, settings.naver_client_secret
    if not cid or not secret:
        return {"query": query, "hits": [], "bodies": [], "note": "네이버 검색 키 미설정"}

    aliases = aliases if aliases else [query]
    seen = seen_titles if seen_titles is not None else set()

    # 블로그: 정확도(sim) + 최신(date) 하이브리드로 후보 풀 확대(정확도-recency 둘 다 포착).
    blog_hits: list[naver_search.SearchHit] = []
    if max_results > 0:
        blog_hits = naver_search.search_blog(cid, secret, query, session, display=max_results, sort=sort)
        if sort == "sim":  # 정확도 패스에 최신 패스를 더해 재랭킹이 recency 도 볼 수 있게
            blog_hits += naver_search.search_blog(
                cid, secret, query, session, display=max_results, sort="date"
            )
    # 뉴스는 최신순(수주·계약은 시의성). display 를 늘려 이벤트 기사 포착률을 높인다.
    news_hits = naver_search.search_news(cid, secret, query, session, display=news_display, sort="date")

    # 관련성 필터 + 제목/URL dedup + 재랭킹(정확도 순위 + recency 결합). 블로그·뉴스 각각.
    blog_ranked = _rerank_dedup(blog_hits, aliases, seen, recency_weight)
    news_ranked = _rerank_dedup(news_hits, aliases, seen, recency_weight)
    hits = blog_ranked + news_ranked

    bodies: list[dict] = []
    # 대표 블로그 본문(재랭킹 상위만 크롤 — 중복 제거 후라 fetch 낭비 없음).
    for h in blog_ranked:
        if len(bodies) >= crawl_bodies:
            break
        body = blog_crawler.crawl_blog(h.link, session)
        if body and body.get("body"):
            bodies.append({**body, "source": "blog"})
    # 대표 뉴스 본문(수주·계약·소송 등 이벤트 실체).
    news_bodies = 0
    for h in news_ranked:
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
