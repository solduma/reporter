"""주가 봉차트 데이터 — 네이버 신형 차트 API(api.stock.naver.com) 래핑.

- 일/주/월봉: {tf} 엔드포인트. 파라미터는 YYYYMMDDHHMM(12자리)여야 한다(8자리면 빈 배열).
- 30분봉: minute 엔드포인트가 minuteUnit=30 을 무시하고 1분봉을 주므로 서버에서 30분 리샘플한다.
  분봉 보존기간이 짧아(~5거래일) 2주는 cron 누적(8단계)으로 완성한다.
무인증(UA 위장). 개인 리서치 용도로 호출을 최소화하고 DB/Redis 캐시로 재호출을 줄인다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.stock.naver.com/chart/domestic/item"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}
_INTRADAY_BUCKET_MIN = 30


@dataclass
class Candle:
    ts: datetime  # 봉 기준 시각(일/주/월봉은 자정)
    open: float
    high: float
    low: float
    close: float
    volume: int
    foreign_ratio: float | None = None


def _get(url: str, params: dict, session: requests.Session) -> list[dict]:
    try:
        resp = session.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("naver chart fetch failed %s: %s", url, e)
        return []
    return data if isinstance(data, list) else []


def fetch_periodic(
    stock_code: str, timeframe: str, start: datetime, end: datetime, session: requests.Session
) -> list[Candle]:
    """일(day)/주(week)/월(month)봉을 조회한다."""
    rows = _get(
        f"{_BASE}/{stock_code}/{timeframe}",
        {"startDateTime": start.strftime("%Y%m%d%H%M"), "endDateTime": end.strftime("%Y%m%d%H%M")},
        session,
    )
    candles: list[Candle] = []
    for r in rows:
        try:
            candles.append(
                Candle(
                    ts=datetime.strptime(r["localDate"], "%Y%m%d"),
                    open=float(r["openPrice"]),
                    high=float(r["highPrice"]),
                    low=float(r["lowPrice"]),
                    close=float(r["closePrice"]),
                    volume=int(r.get("accumulatedTradingVolume", 0)),
                    foreign_ratio=r.get("foreignRetentionRate"),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue  # 형식 이탈 행은 건너뛴다
    return candles


def _resample_30min(minute_rows: list[dict]) -> list[Candle]:
    """1분봉을 30분봉으로 리샘플한다(OHLC 집계, 거래량 합산)."""
    buckets: dict[datetime, list[dict]] = {}
    for r in minute_rows:
        try:
            ts = datetime.strptime(r["localDateTime"], "%Y%m%d%H%M%S")
        except (KeyError, ValueError):
            continue
        floored = ts.replace(minute=(ts.minute // _INTRADAY_BUCKET_MIN) * _INTRADAY_BUCKET_MIN, second=0)
        buckets.setdefault(floored, []).append(r)

    candles: list[Candle] = []
    for bucket_ts in sorted(buckets):
        # 소스 정렬에 의존하지 않도록 버킷 내부를 시각순으로 정렬(open/close 정확성).
        rows = sorted(buckets[bucket_ts], key=lambda r: r.get("localDateTime", ""))
        try:
            highs = [float(r["highPrice"]) for r in rows]
            lows = [float(r["lowPrice"]) for r in rows]
            candles.append(
                Candle(
                    ts=bucket_ts,
                    open=float(rows[0]["openPrice"]),
                    high=max(highs),
                    low=min(lows),
                    close=float(rows[-1]["currentPrice"]),  # 분봉 종가는 currentPrice
                    # accumulatedTradingVolume 은 분봉 엔드포인트에선 per-bar 이므로 합산이 맞다.
                    volume=sum(int(r.get("accumulatedTradingVolume", 0)) for r in rows),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return candles


def fetch_intraday_30min(stock_code: str, session: requests.Session) -> list[Candle]:
    """네이버 분봉(1분)을 받아 30분봉으로 리샘플한다. 가용 구간(최근 ~5거래일)만."""
    rows = _get(f"{_BASE}/{stock_code}/minute", {"minuteUnit": 1}, session)
    return _resample_30min(rows)
