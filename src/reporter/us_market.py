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
_FX_BASE = "https://api.stock.naver.com/marketindex/exchange/{symbol}"
_STOCK_BASE = "https://api.stock.naver.com/stock/{symbol}/basic"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_US_INDICES = [(".DJI", "다우"), (".IXIC", "나스닥"), (".INX", "S&P500")]
_KR_INDICES = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]
# 환율: 네이버 marketindex/exchange. 응답이 exchangeInfo 로 중첩되고 필드명이 지수와 달라 별도 파싱.
# 코스피·코스닥과 한 줄에 두려 원/달러만 노출한다.
_FX_RATES = [("FX_USDKRW", "원/달러")]
# 미국 섹터 선행 분석용 프록시. 네이버 index API 로 안정 조회되는 심볼만 쓴다
# (GICS 세부섹터 지수 .SP500-45 등은 종가 결측이라 제외). 종목 업종을 이 셋 중 하나에 매핑한다.
_US_SECTOR_PROXIES = [(".SOX", "미국 반도체"), (".IXIC", "미국 기술주"), (".INX", "미국 대형주")]

# 업종 키워드 → 미국 프록시 심볼. 반도체·기술 위주로 선행 신호가 뚜렷한 것만 SOX/IXIC 로,
# 그 외는 대형주 벤치마크(.INX)로 떨군다. 소문자 부분일치로 매칭한다.
_PROXY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("반도체", "메모리", "디스플레이", "장비", "소재·부품", "전공정"), ".SOX"),
    (("소프트웨어", "인터넷", "게임", "it", "플랫폼", "미디어", "엔터"), ".IXIC"),
]
_DEFAULT_PROXY = ".INX"

# 대시보드 최상단에서 매 로드마다 네이버를 반복 호출하지 않도록 하는 프로세스 인메모리 캐시.
_CACHE_TTL = 120.0  # 초
_us_cache: tuple[float, list[IndexQuote]] | None = None
_kr_cache: tuple[float, list[IndexQuote]] | None = None
_proxy_cache: tuple[float, list[IndexQuote]] | None = None
_fx_cache: tuple[float, list[IndexQuote]] | None = None


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


def _parse_fx(name: str, data: dict) -> IndexQuote | None:
    """환율 응답(exchangeInfo 중첩) → IndexQuote. 필드명이 지수와 달라 별도 파싱."""
    info = data.get("exchangeInfo") or {}
    close = info.get("closePrice")
    if not close:
        return None
    cmp_code = (info.get("fluctuationsType") or {}).get("code")
    rising = True if cmp_code == "2" else False if cmp_code == "5" else None
    return IndexQuote(
        name=name,
        close=str(close),
        change=str(info.get("fluctuations", "")),
        change_ratio=str(info.get("fluctuationsRatio", "")),
        rising=rising,
    )


def fetch_exchange_rates(session: requests.Session | None = None) -> list[IndexQuote]:
    """원/달러 등 환율을 조회한다. 지수와 같은 IndexQuote 로 반환해 대시보드에서 공용."""
    global _fx_cache
    if _fx_cache and time.monotonic() - _fx_cache[0] < _CACHE_TTL:
        return _fx_cache[1]
    session = session or requests.Session()
    quotes: list[IndexQuote] = []
    for symbol, name in _FX_RATES:
        try:
            resp = session.get(_FX_BASE.format(symbol=symbol), headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("fx fetch failed %s: %s", symbol, e)
            continue
        quote = _parse_fx(name, data)
        if quote:
            quotes.append(quote)
    if quotes:
        _fx_cache = (time.monotonic(), quotes)
    return quotes


def fetch_us_sector_proxies(session: requests.Session | None = None) -> list[IndexQuote]:
    """미국 섹터 선행 프록시(.SOX/.IXIC/.INX) 종가·등락. 미국 세계지수 엔드포인트 사용."""
    global _proxy_cache
    if _proxy_cache and time.monotonic() - _proxy_cache[0] < _CACHE_TTL:
        return _proxy_cache[1]
    quotes = _fetch_indices(_US_BASE, _US_SECTOR_PROXIES, session or requests.Session())
    if quotes:
        _proxy_cache = (time.monotonic(), quotes)
    return quotes


def fetch_us_stock_quotes(
    symbols: list[tuple[str, str]], session: requests.Session | None = None
) -> list[IndexQuote]:
    """미국 개별종목 (심볼, 표시명) 목록의 시세를 조회한다. 실패한 종목은 빠진다.

    표시명(한글)을 name 으로 쓰고 지수와 같은 IndexQuote 로 반환한다(캐시 없음 — 목록 가변).
    """
    session = session or requests.Session()
    quotes: list[IndexQuote] = []
    for symbol, label in symbols:
        try:
            resp = session.get(_STOCK_BASE.format(symbol=symbol), headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("us stock fetch failed %s: %s", symbol, e)
            continue
        quote = _parse_quote(label, data)
        if quote:
            quotes.append(quote)
    return quotes


def map_industry_to_proxy(industry: str | None, market: str | None = None) -> str:
    """업종 라벨(+시장)을 미국 프록시 심볼(.SOX/.IXIC/.INX)로 매핑한다.

    업종 키워드가 없으면 시장으로 폴백(KOSDAQ→기술주 .IXIC, 그 외→대형주 .INX).
    """
    if industry:
        low = industry.lower()
        for keywords, proxy in _PROXY_KEYWORDS:
            if any(k in low for k in keywords):
                return proxy
    if market == "KOSDAQ":
        return ".IXIC"
    return _DEFAULT_PROXY
