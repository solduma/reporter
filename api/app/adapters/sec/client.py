"""SEC EDGAR 클라이언트 — ticker→CIK 매핑 + companyfacts(XBRL 재무) 조회(driven adapter).

무인증이나 연락처 명시 User-Agent 필수(SEC 정책). ticker 매핑(company_tickers.json)은
전종목 한 파일이라 프로세스 캐시한다. companyfacts 는 종목별 큰 JSON(수 MB)이므로 그대로 반환.
"""

from __future__ import annotations

import logging

import requests

from app.adapters.sec import throttle
from app.config import Settings

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# ticker(대문자) → {cik, name} 프로세스 캐시(전종목 ~9천, 한 파일).
_ticker_map: dict[str, dict] | None = None


def _headers(settings: Settings) -> dict:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def ticker_map(settings: Settings, session: requests.Session | None = None) -> dict[str, dict]:
    """전종목 ticker→{cik:int, name:str} 매핑. 캐시. 실패 시 빈 dict."""
    global _ticker_map
    if _ticker_map is not None:
        return _ticker_map
    session = session or requests.Session()
    try:
        resp = throttle.get(session, _TICKERS_URL, headers=_headers(settings), timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("SEC ticker map fetch failed: %s", e)
        return {}
    # 응답은 {"0": {"cik_str":.., "ticker":.., "title":..}, ...}
    out = {
        row["ticker"].upper(): {"cik": int(row["cik_str"]), "name": row["title"]}
        for row in data.values()
        if row.get("ticker")
    }
    _ticker_map = out
    return out


def resolve_cik(settings: Settings, ticker: str, session: requests.Session | None = None) -> int | None:
    """ticker(대소문자 무관) → CIK. 없으면 None."""
    return (ticker_map(settings, session).get(ticker.upper()) or {}).get("cik")


def company_name(settings: Settings, ticker: str, session: requests.Session | None = None) -> str | None:
    return (ticker_map(settings, session).get(ticker.upper()) or {}).get("name")


def fetch_company_facts(
    settings: Settings, cik: int, session: requests.Session | None = None
) -> dict | None:
    """CIK 의 companyfacts(XBRL 전체 재무 사실) JSON. 실패·없음이면 None."""
    session = session or requests.Session()
    try:
        resp = throttle.get(
            session, _FACTS_URL.format(cik=cik), headers=_headers(settings), timeout=30
        )
        if resp.status_code == 404:
            return None  # 해당 CIK 재무사실 없음
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("SEC companyfacts fetch failed CIK%s: %s", cik, e)
        return None
