"""시장 코드 → MarketDataPort 어댑터 선택."""

from __future__ import annotations

from app.adapters.market.kr import KrMarketDataAdapter
from app.adapters.market.us import UsMarketDataAdapter
from app.ports.market_data import MarketDataPort

_KR = KrMarketDataAdapter()
_US = UsMarketDataAdapter()


def get_market_data(market: str = "KR") -> MarketDataPort:
    """시장 코드로 시세 어댑터를 고른다(기본 KR). 어댑터는 무상태라 싱글턴 재사용."""
    return _US if market == "US" else _KR
