"""기사 본문 추출 — Google News 링크를 headless Chrome 으로 렌더해 원문 텍스트를 얻는다.

Google News RSS 링크는 서버 요청으로 원문에 도달하지 못하므로(JS 리다이렉트),
시스템 Chrome 을 headless 로 띄워 최종 DOM 텍스트를 추출한다. 무겁고 실패 가능성이
있어 소수 기사에만 쓰고, 실패 시 빈 문자열을 반환해 호출측이 제목으로 폴백한다.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _chrome_path() -> str | None:
    for cand in _CHROME_CANDIDATES:
        if cand.startswith("/"):
            if shutil.which(cand) or __import__("os").path.exists(cand):
                return cand
        elif shutil.which(cand):
            return cand
    return None


def fetch_article_text(url: str, max_chars: int = 2000, timeout: int = 25) -> str:
    """headless Chrome 으로 기사 본문 텍스트를 추출한다. 실패 시 빈 문자열."""
    chrome = _chrome_path()
    if not chrome or not url:
        return ""
    try:
        result = subprocess.run(
            [
                chrome, "--headless", "--disable-gpu", "--no-sandbox",
                "--dump-dom", "--virtual-time-budget=12000", url,
            ],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("article render failed %s: %s", url[:60], e)
        return ""

    html = result.stdout
    # <body> 이후 태그 제거 후 텍스트만. 본문 정밀 추출은 아니지만 GLM 종합엔 충분.
    body = html.split("<body", 1)[-1]
    text = _TAG_RE.sub(" ", body)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]
