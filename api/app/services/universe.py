"""종목 유니버스 — 네이버 marketValue API(무키)로 전종목 시총·모멘텀·거래대금 수집.

스몰캡 성장 스크리너의 기반 데이터. KOSDAQ 우선(스몰캡 성장주 집중), 페이지네이션으로
전 종목을 순회한다. 비공식 API 라 방어적 파싱 + 페이지 간 딜레이.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_BASE = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_PAGE_SIZE = 100
_MAX_PAGES = 60  # 안전 상한(코스닥 ~19p, 코스피 ~10p)
_PAGE_DELAY = 0.3


@dataclass
class UniverseRow:
    stock_code: str
    market: str  # KOSPI | KOSDAQ
    stock_name: str
    stock_type: str  # stock | etf | etn ... (ETF/ETN 제외용)
    close_price: int | None
    change_pct: float | None
    market_cap: int | None  # 원 단위
    trading_value: int | None  # 거래대금(원)
    three_month_rate: float | None  # 3개월 수익률(%) — marketValue 는 대개 결측


def _int(text) -> int | None:
    try:
        return int(str(text).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _float(text) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except (ValueError, TypeError):
        return None  # 'N/A' 등 → None


def _parse(row: dict, market: str) -> UniverseRow | None:
    code = row.get("itemCode")
    if not code:
        return None
    return UniverseRow(
        stock_code=code,
        market=market,
        stock_name=row.get("stockName", ""),
        stock_type=row.get("stockEndType", "stock"),
        close_price=_int(row.get("closePriceRaw")),
        change_pct=_float(row.get("fluctuationsRatio")),
        market_cap=_int(row.get("marketValueRaw")),
        trading_value=_int(row.get("accumulatedTradingValueRaw")),
        three_month_rate=_float(row.get("threeMonthEarningRate")),
    )


def fetch_market(market: str, session: requests.Session | None = None) -> list[UniverseRow]:
    """한 시장(KOSPI|KOSDAQ)의 전 종목을 페이지네이션으로 수집한다."""
    session = session or requests.Session()
    rows: list[UniverseRow] = []
    for page in range(1, _MAX_PAGES + 1):
        try:
            resp = session.get(
                _BASE.format(market=market),
                params={"page": page, "pageSize": _PAGE_SIZE},
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("universe fetch failed %s p%d: %s", market, page, e)
            break

        stocks = data.get("stocks") or []
        if not stocks:
            break
        rows.extend(r for r in (_parse(s, market) for s in stocks) if r)
        if page * _PAGE_SIZE >= data.get("totalCount", 0):
            break
        time.sleep(_PAGE_DELAY)

    logger.info("universe %s: %d rows", market, len(rows))
    return rows


def fetch_universe(markets: tuple[str, ...] = ("KOSDAQ", "KOSPI")) -> list[UniverseRow]:
    """지정 시장들의 전 종목을 수집한다(기본 코스닥 우선 + 코스피)."""
    session = requests.Session()
    rows: list[UniverseRow] = []
    for market in markets:
        rows.extend(fetch_market(market, session))
    return rows
