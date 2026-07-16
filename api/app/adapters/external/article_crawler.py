"""범용 뉴스/기사 본문 크롤러 — 딥다이브가 뉴스(수주·계약·소송 등) 본문을 읽게 한다.

블로그 전용 크롤러(blog_crawler)와 달리 임의 뉴스 도메인의 기사 본문을 추출한다. readability 라이브러리
없이, 흔한 기사 본문 컨테이너 셀렉터 우선 + 폴백으로 <p> 밀도가 높은 블록을 택하는 경량 휴리스틱.
네이버 뉴스는 본문 컨테이너가 명확(#dic_area·#newsct_article)해 잘 잡히고, 언론사 원문도 대개 커버된다.
"""

from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_MAX_CHARS = 8000

# 흔한 기사 본문 컨테이너(우선순위 순). 네이버 뉴스·주요 언론사·더벨 등.
_ARTICLE_SELECTORS = (
    "#dic_area", "#newsct_article", "#articeBody", "#article-view-content-div",
    "#articleBody", "#news_body_area", ".article_body", ".article-body",
    ".news_end", ".art_txt", "#CmAdContent", "article",
)
# 본문에서 제거할 잡음 태그.
_NOISE = ("script", "style", "figure", "figcaption", "iframe", "aside", "nav", "footer")


def _clean(node) -> str:
    for tag in node.find_all(_NOISE):
        tag.decompose()
    text = node.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text).strip()


def crawl_article(url: str, session: requests.Session) -> dict | None:
    """임의 뉴스/기사 URL → {title, body, url}. 본문 추출 실패 시 None.

    블로그(blog.naver.com)는 blog_crawler 가 담당하므로 여기선 그 외 도메인을 처리한다."""
    try:
        resp = session.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        logger.warning("article crawl failed %s: %s", url, e)
        return None
    soup = BeautifulSoup(html, "html.parser")

    container = None
    for sel in _ARTICLE_SELECTORS:
        container = soup.select_one(sel)
        if container:
            break
    if container is None:
        # 폴백: <p> 텍스트가 가장 많이 모인 블록을 본문으로 추정.
        best, best_len = None, 0
        for div in soup.find_all(["div", "section"]):
            ps = div.find_all("p", recursive=False)
            plen = sum(len(p.get_text(strip=True)) for p in ps)
            if plen > best_len:
                best, best_len = div, plen
        if best is None or best_len < 200:  # 본문이라 볼 만한 최소 분량 미달
            return None
        container = best

    body = _clean(container)
    if len(body) < 150:  # 추출은 됐으나 실질 본문 부족(광고·안내만)
        return None
    title_el = soup.select_one("h1, h2, .media_end_head_headline, .article_head, title")
    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "",
        "body": body[:_MAX_CHARS],
    }
