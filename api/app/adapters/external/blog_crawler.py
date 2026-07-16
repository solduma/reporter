"""네이버 블로그 본문 크롤러 — 딥다이브 웹 리서치의 본문 수집 단계.

blog.naver.com/{id}/{logNo} 는 프레임셋만 반환하고 본문은 #mainFrame iframe(PostView.naver) 안에
있다. logNo 로 PostView.naver 를 직접 GET 해 본문 컨테이너(.se-main-container, 구버전 #postViewArea)
를 파싱한다. 삭제·비공개 글은 200 이지만 컨테이너 부재 → None(호출측 방어). requests+bs4(설치됨).
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
_POSTVIEW = "https://blog.naver.com/PostView.naver"
_MAX_CHARS = 8000  # 본문 상한(프롬프트 토큰 통제)


def parse_blog_url(url: str) -> tuple[str, str] | None:
    """blog.naver.com/{id}/{logNo} 또는 ...?blogId=&logNo= 에서 (blogId, logNo). 아니면 None."""
    m = re.search(r"blog\.naver\.com/([^/?]+)/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    bid = re.search(r"blogId=([^&]+)", url)
    lno = re.search(r"logNo=(\d+)", url)
    if bid and lno:
        return bid.group(1), lno.group(1)
    return None


def crawl_blog(url: str, session: requests.Session) -> dict | None:
    """네이버 블로그 URL → {title, body, url}. 네이버 블로그가 아니거나 본문 없으면 None."""
    parsed = parse_blog_url(url)
    if not parsed:
        return None
    blog_id, log_no = parsed
    post_url = f"{_POSTVIEW}?blogId={blog_id}&logNo={log_no}&redirect=Dlog&directAccess=false"
    try:
        html = session.get(
            post_url, headers={**_HEADERS, "Referer": f"https://blog.naver.com/{blog_id}"}, timeout=10
        ).text
    except requests.RequestException as e:
        logger.warning("blog crawl failed %s/%s: %s", blog_id, log_no, e)
        return None
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
    if not container:
        return None  # 삭제·비공개·구조 변경
    title_el = soup.select_one(".se-title-text") or soup.select_one(".pcol1")
    body = container.get_text("\n", strip=True)
    return {
        "url": f"https://blog.naver.com/{blog_id}/{log_no}",
        "title": title_el.get_text(strip=True) if title_el else "",
        "body": body[:_MAX_CHARS],
    }
