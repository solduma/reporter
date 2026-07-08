"""URL 단축 — TinyURL API + 파일 캐시.

텔레그램 메시지의 긴 PDF/기사 링크를 tinyurl.com/xxxx 로 줄인다. 같은 URL 은
캐시해 재요청하지 않는다(캐시 파일은 logs_dir 아래 JSON). 실패 시 원본 URL 반환.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_API = "https://tinyurl.com/api-create.php"


class UrlShortener:
    def __init__(self, cache_path: Path, session: requests.Session | None = None):
        self._cache_path = cache_path
        self._session = session or requests.Session()
        self._cache: dict[str, str] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def shorten(self, url: str) -> str:
        """단축 URL 을 반환한다. 캐시 히트 시 즉시, 실패 시 원본 그대로."""
        if not url:
            return url
        if url in self._cache:
            return self._cache[url]
        try:
            resp = self._session.get(_API, params={"url": url}, timeout=10)
            resp.raise_for_status()
            short = resp.text.strip()
        except requests.RequestException as e:
            logger.warning("shorten failed %s: %s", url, e)
            return url
        if not short.startswith("http"):  # 'Error' 등 비정상 응답
            logger.warning("shorten rejected %s: %s", url, short[:60])
            return url
        self._cache[url] = short
        self._flush()
        return short

    def _flush(self) -> None:
        try:
            self._cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("shorten cache write failed: %s", e)
