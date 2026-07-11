"""미국(US) 시세 어댑터 — 네이버 foreign 일/주/월봉. 30분봉은 미지원(빈 리스트)."""

from __future__ import annotations

from datetime import datetime

import requests

from app.adapters.market import naver as chart


class UsMarketDataAdapter:
    """MarketDataPort(US). 네이버 foreign 봉. 외국인비율·30분봉 없음."""

    def fetch_periodic(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[chart.Candle]:
        return chart.fetch_periodic_foreign(symbol, timeframe, start, end, requests.Session())

    def fetch_intraday_30min(self, code: str) -> list[chart.Candle]:
        return []  # 미국 30분봉 소스 없음
