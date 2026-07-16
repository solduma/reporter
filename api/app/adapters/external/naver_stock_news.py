"""네이버 증권 종목 뉴스 — 종목코드 직결 뉴스 목록(오매칭 없음).

키워드 검색(search_news)은 타종목 기사를 느슨히 매칭하는 문제가 있어, 종목에 직접 연결된 뉴스만
주는 m.stock.naver.com API 를 쓴다. 제목·날짜·언론사·요약(body)·원문링크(mobileNewsUrl) 제공.
전체 본문은 mobileNewsUrl 을 article_crawler 로 크롤. 인증 불필요.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_URL = "https://m.stock.naver.com/api/news/stock/{code}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    )
}


@dataclass
class StockNews:
    title: str
    summary: str  # 기사 요약(body 필드, 수백자)
    press: str
    datetime: str  # YYYYMMDDHHMM
    url: str  # 원문(n.news.naver.com) — 전체 본문 크롤용


def fetch_stock_news(code: str, session: requests.Session, pages: int = 2, page_size: int = 20) -> list[StockNews]:
    """종목코드 직결 뉴스 목록(최신순). 종목에 실제 연결된 기사만 — 키워드 오매칭 없음."""
    out: list[StockNews] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        try:
            resp = session.get(
                _URL.format(code=code), headers=_HEADERS,
                params={"pageSize": page_size, "page": page}, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("naver stock news failed %s p%d: %s", code, page, e)
            break
        items = data[0].get("items", []) if isinstance(data, list) and data else []
        if not items:
            break
        for it in items:
            url = (it.get("mobileNewsUrl") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(StockNews(
                title=(it.get("titleFull") or it.get("title") or "").strip(),
                summary=(it.get("body") or "").strip(),
                press=(it.get("officeName") or "").strip(),
                datetime=(it.get("datetime") or "").strip(),
                url=url,
            ))
    return out
