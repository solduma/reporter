"""국내(KR) 시세 어댑터 — 네이버 우선, 비면 KIS 폴백. 30분봉 지원."""

from __future__ import annotations

from datetime import datetime

import requests

from app.adapters.market import naver as chart
from app.config import get_settings


class KrMarketDataAdapter:
    """MarketDataPort(KR). 네이버→KIS 폴백 일/주/월봉 + 네이버 30분봉."""

    def fetch_periodic(
        self, code: str, timeframe: str, start: datetime, end: datetime
    ) -> list[chart.Candle]:
        session = requests.Session()
        return chart.fetch_periodic_with_fallback(get_settings(), code, timeframe, start, end, session)

    def fetch_intraday_30min(self, code: str) -> list[chart.Candle]:
        return chart.fetch_intraday_30min(code, requests.Session())
