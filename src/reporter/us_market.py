"""미국 3대 지수 시세 — 네이버 세계지수 API (무키).

GET https://api.stock.naver.com/index/{symbol}/basic → closePrice/등락/등락률/현지시각.
news.search 와 동일하게 실패는 조용히 흡수(해당 지수 skip).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.stock.naver.com/index/{symbol}/basic"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_INDICES = [(".DJI", "다우"), (".IXIC", "나스닥"), (".INX", "S&P500")]


@dataclass
class IndexQuote:
    name: str
    close: str  # 표시 문자열 (예 '52,925.15')
    change: str  # 전일 대비 (예 '-130.76')
    change_ratio: str  # 등락률 % (예 '-0.25')
    rising: bool | None  # 상승 True / 하락 False / 판단불가 None


def fetch_us_indices(session: requests.Session | None = None) -> list[IndexQuote]:
    """다우·나스닥·S&P500 종가·등락을 조회한다. 실패한 지수는 결과에서 빠진다."""
    session = session or requests.Session()
    quotes: list[IndexQuote] = []
    for symbol, name in _INDICES:
        try:
            resp = session.get(_BASE.format(symbol=symbol), headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("us index fetch failed %s: %s", symbol, e)
            continue

        close = data.get("closePrice")
        if not close:
            continue
        cmp_code = (data.get("compareToPreviousPrice") or {}).get("code")
        # 네이버 코드: 2=상승, 5=하락 (그 외는 보합/판단불가)
        rising = True if cmp_code == "2" else False if cmp_code == "5" else None
        quotes.append(
            IndexQuote(
                name=name,
                close=str(close),
                change=str(data.get("compareToPreviousClosePrice", "")),
                change_ratio=str(data.get("fluctuationsRatio", "")),
                rising=rising,
            )
        )
    return quotes
