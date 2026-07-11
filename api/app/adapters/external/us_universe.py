"""US 유니버스 소스 — 시드(S&P500 CSV + 나스닥 보충) + 네이버 종목 시세(driven adapter).

시총·PER·PBR·EPS·거래대금은 네이버 stock/{sym}/basic 이 종목당 한 번에 준다(KR 스크리너와
소스·계산 일관). 시드는 S&P500 datasets CSV(GICS 섹터 포함) + 대형 비-S&P 나스닥 보충 목록.
심볼 접미사(.O/bare)는 resolve_us_symbol 이 자동 해석한다.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass

import requests

from reporter import us_market

logger = logging.getLogger(__name__)

_SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_STOCK_BASE = "https://api.stock.naver.com/stock/{symbol}/basic"

# S&P500 에 없지만 관심 큰 대형 나스닥/기타 종목 보충(중복은 시드 단계에서 dedup).
_SUPPLEMENT: list[tuple[str, str]] = [
    ("AVGO", "Technology"), ("ASML", "Technology"), ("PDD", "Consumer Discretionary"),
    ("MELI", "Consumer Discretionary"), ("MSTR", "Technology"), ("COIN", "Financials"),
    ("ARM", "Technology"), ("SMCI", "Technology"), ("PLTR", "Technology"),
    ("SNOW", "Technology"), ("DDOG", "Technology"), ("CRWD", "Technology"),
]


@dataclass
class UsUniverseRow:
    ticker: str
    naver_symbol: str  # .O/bare 해석된 심볼(차트·재조회 공용)
    name: str
    exchange: str | None  # NASDAQ | NYSE | ...
    sector: str | None
    close_price: float | None
    change_pct: float | None
    market_cap: float | None  # USD (종가 x 상장주식수)
    trading_value: float | None  # 거래대금 USD
    per: float | None
    pbr: float | None
    eps: float | None
    high_52w: float | None
    low_52w: float | None


def seed_tickers(session: requests.Session | None = None) -> list[tuple[str, str | None]]:
    """유니버스 시드 (ticker, sector). S&P500 CSV + 보충 목록, dedup. CSV 실패 시 보충만."""
    session = session or requests.Session()
    out: dict[str, str | None] = {}
    try:
        resp = session.get(_SP500_CSV, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            sym = (row.get("Symbol") or "").strip()
            if sym:
                # S&P CSV 는 BRK.B 처럼 점 표기 — 네이버/일반 티커는 그대로 두고 소스에서 해석.
                out[sym] = (row.get("GICS Sector") or "").strip() or None
    except (requests.RequestException, ValueError) as e:
        logger.warning("S&P500 seed fetch failed: %s", e)
    for sym, sector in _SUPPLEMENT:
        out.setdefault(sym, sector)
    return sorted(out.items())


def _num(text: str | None) -> float | None:
    """'32.26배'·'26.14%'·'210.96' → float. 실패 시 None."""
    if not text:
        return None
    cleaned = str(text).replace(",", "").rstrip("배%원").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _won_eok_usd(text: str | None) -> float | None:
    """'310억 USD'·'5조 1,052억 USD' → USD 실수. 한국어 조/억 단위 파싱."""
    if not text:
        return None
    s = str(text).replace("USD", "").replace(",", "").strip()
    total = 0.0
    matched = False
    if "조" in s:
        jo, s = s.split("조", 1)
        total += _num(jo) * 1e12 if _num(jo) else 0
        matched = True
    if "억" in s:
        eok = s.split("억", 1)[0]
        total += _num(eok) * 1e8 if _num(eok) else 0
        matched = True
    return total if matched else _num(s)


def _totals(basic: dict) -> dict[str, str]:
    return {it.get("code"): it.get("value") for it in (basic.get("stockItemTotalInfos") or [])}


def fetch_row(ticker: str, sector: str | None, session: requests.Session | None = None) -> UsUniverseRow | None:
    """네이버에서 한 종목의 유니버스 행을 만든다. 심볼 미해석·시세 없음이면 None."""
    session = session or requests.Session()
    resolved = us_market.resolve_us_symbol(ticker, session)
    if resolved is None:
        return None
    symbol, _q = resolved
    try:
        resp = session.get(_STOCK_BASE.format(symbol=symbol), headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        b = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("us universe fetch failed %s: %s", ticker, e)
        return None

    close = _num(b.get("closePrice"))
    shares = b.get("countOfListedStock")
    market_cap = (close * shares) if (close and shares) else None
    totals = _totals(b)
    exch = (b.get("stockExchangeType") or {}).get("name")
    return UsUniverseRow(
        ticker=ticker,
        naver_symbol=symbol,
        name=b.get("stockName") or ticker,
        exchange=exch,
        sector=sector,
        close_price=close,
        change_pct=_num(b.get("fluctuationsRatio")),
        market_cap=market_cap,
        trading_value=_won_eok_usd(totals.get("accumulatedTradingValue")),
        per=_num(totals.get("per")),
        pbr=_num(totals.get("pbr")),
        eps=_num(totals.get("eps")),
        high_52w=_num(totals.get("highPriceOf52Weeks")),
        low_52w=_num(totals.get("lowPriceOf52Weeks")),
    )
