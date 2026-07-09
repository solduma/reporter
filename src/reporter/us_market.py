"""지수 시세 — 네이버 지수 API (무키).

- 미국: GET https://api.stock.naver.com/index/{symbol}/basic (.DJI/.IXIC/.INX)
- 국내: GET https://m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/basic
두 응답 모두 closePrice/compareToPreviousClosePrice/fluctuationsRatio/compareToPreviousPrice.code
필드가 동일하다. news.search 와 동일하게 실패는 조용히 흡수(해당 지수 skip).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_US_BASE = "https://api.stock.naver.com/index/{symbol}/basic"
_KR_BASE = "https://m.stock.naver.com/api/index/{symbol}/basic"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_US_INDICES = [(".DJI", "다우"), (".IXIC", "나스닥"), (".INX", "S&P500")]
_KR_INDICES = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]

# 대시보드 최상단에서 매 로드마다 네이버를 반복 호출하지 않도록 하는 프로세스 인메모리 캐시.
_CACHE_TTL = 120.0  # 초
_us_cache: tuple[float, list[IndexQuote]] | None = None
_kr_cache: tuple[float, list[IndexQuote]] | None = None


@dataclass
class IndexQuote:
    name: str
    close: str  # 표시 문자열 (예 '52,925.15')
    change: str  # 전일 대비 (예 '-130.76')
    change_ratio: str  # 등락률 % (예 '-0.25')
    rising: bool | None  # 상승 True / 하락 False / 판단불가 None


def _parse_quote(name: str, data: dict) -> IndexQuote | None:
    """네이버 지수 응답 dict → IndexQuote. 종가가 없으면 None."""
    close = data.get("closePrice")
    if not close:
        return None
    cmp_code = (data.get("compareToPreviousPrice") or {}).get("code")
    # 네이버 코드: 2=상승, 5=하락 (그 외는 보합/판단불가)
    rising = True if cmp_code == "2" else False if cmp_code == "5" else None
    return IndexQuote(
        name=name,
        close=str(close),
        change=str(data.get("compareToPreviousClosePrice", "")),
        change_ratio=str(data.get("fluctuationsRatio", "")),
        rising=rising,
    )


def _fetch_indices(
    base: str, indices: list[tuple[str, str]], session: requests.Session
) -> list[IndexQuote]:
    quotes: list[IndexQuote] = []
    for symbol, name in indices:
        try:
            resp = session.get(base.format(symbol=symbol), headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("index fetch failed %s: %s", symbol, e)
            continue
        quote = _parse_quote(name, data)
        if quote:
            quotes.append(quote)
    return quotes


def fetch_us_indices(session: requests.Session | None = None) -> list[IndexQuote]:
    """다우·나스닥·S&P500 종가·등락을 조회한다. 실패한 지수는 결과에서 빠진다.

    _CACHE_TTL 초 안의 재호출은 캐시된 결과를 돌려준다(대시보드 로딩 지연 완화).
    """
    global _us_cache
    if _us_cache and time.monotonic() - _us_cache[0] < _CACHE_TTL:
        return _us_cache[1]
    quotes = _fetch_indices(_US_BASE, _US_INDICES, session or requests.Session())
    if quotes:  # 부분 성공만 캐시(전량 실패는 다음 호출에서 재시도)
        _us_cache = (time.monotonic(), quotes)
    return quotes


def fetch_kr_indices(session: requests.Session | None = None) -> list[IndexQuote]:
    """코스피·코스닥 종가·등락을 조회한다. 실패한 지수는 결과에서 빠진다."""
    global _kr_cache
    if _kr_cache and time.monotonic() - _kr_cache[0] < _CACHE_TTL:
        return _kr_cache[1]
    quotes = _fetch_indices(_KR_BASE, _KR_INDICES, session or requests.Session())
    if quotes:
        _kr_cache = (time.monotonic(), quotes)
    return quotes
