"""KRX 개방 API(data-dbg.krx.co.kr) — 종목 기본정보(상장주식수 등).

주식수는 밸류에이션(PBR·PSR·시총)에 필요한데, 네이버는 현재값만 준다. KRX 종목기본정보는
by-date 로 과거 시점 실제 상장주식수를 줘(액면분할·증자 반영) 밸류 정확도를 높인다.
AUTH_KEY 헤더 인증. 무료.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://data-dbg.krx.co.kr/svc/apis"
# 유가증권(stk)·코스닥(ksq) 종목기본정보. 한 요청이 해당일 전 종목을 준다.
_ISU_BASE = {
    "KOSPI": f"{_BASE}/sto/stk_isu_base_info",
    "KOSDAQ": f"{_BASE}/sto/ksq_isu_base_info",
}


def fetch_shares_by_date(
    api_key: str, bas_dd: str, session: requests.Session, market: str = "KOSPI"
) -> dict[str, int]:
    """기준일(bas_dd=YYYYMMDD) 전 종목의 상장주식수 맵 {단축코드: 주식수}. 실패 시 빈 dict.

    market 은 KOSPI|KOSDAQ. 두 시장을 각각 호출해 합치는 건 호출측 책임.
    """
    url = _ISU_BASE.get(market)
    if not url:
        return {}
    try:
        resp = session.get(url, params={"basDd": bas_dd}, headers={"AUTH_KEY": api_key}, timeout=20)
        resp.raise_for_status()
        rows = resp.json().get("OutBlock_1") or []
    except (requests.RequestException, ValueError) as e:
        logger.warning("krx isu_base fetch failed %s %s: %s", market, bas_dd, e)
        return {}
    out: dict[str, int] = {}
    for row in rows:
        code = (row.get("ISU_SRT_CD") or "").strip()
        raw = (row.get("LIST_SHRS") or "").replace(",", "").strip()
        if not code or not raw:
            continue
        try:
            out[code] = int(raw)
        except ValueError:
            continue
    return out


def fetch_shares(api_key: str, bas_dd: str, code: str, session: requests.Session) -> int | None:
    """단일 종목의 기준일 상장주식수. KOSPI→KOSDAQ 순으로 찾는다. 없으면 None."""
    for market in ("KOSPI", "KOSDAQ"):
        shares = fetch_shares_by_date(api_key, bas_dd, session, market).get(code)
        if shares:
            return shares
    return None
