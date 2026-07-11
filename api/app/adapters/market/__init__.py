"""시세 소스 어댑터 — MarketDataPort 구현(시장별).

KR=네이버(→KIS 폴백)·30분봉, US=네이버 foreign(30분봉 미지원). get_market_data(market)로 선택.
"""

from app.adapters.market.factory import get_market_data
from app.adapters.market.kr import KrMarketDataAdapter
from app.adapters.market.us import UsMarketDataAdapter

__all__ = ["KrMarketDataAdapter", "UsMarketDataAdapter", "get_market_data"]
