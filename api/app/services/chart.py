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
_FOREIGN_BASE = "https://api.stock.naver.com/chart/foreign/item"
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


def _parse_periodic(rows: list[dict]) -> list[Candle]:
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
                    foreign_ratio=r.get("foreignRetentionRate"),  # 미국(foreign)은 없음 → None
                )
            )
        except (KeyError, ValueError, TypeError):
            continue  # 형식 이탈 행은 건너뛴다
    return candles


def fetch_periodic(
    stock_code: str, timeframe: str, start: datetime, end: datetime, session: requests.Session
) -> list[Candle]:
    """국내 종목/ETF 일(day)/주(week)/월(month)봉을 조회한다."""
    rows = _get(
        f"{_BASE}/{stock_code}/{timeframe}",
        {"startDateTime": start.strftime("%Y%m%d%H%M"), "endDateTime": end.strftime("%Y%m%d%H%M")},
        session,
    )
    return _parse_periodic(rows)


def fetch_periodic_with_fallback(
    settings, stock_code: str, timeframe: str, start: datetime, end: datetime,
    session: requests.Session,
) -> list[Candle]:
    """네이버 우선, 비면 KIS 로 폴백해 국내 일/주/월봉을 조회한다.

    KIS 는 kis 모듈을 지연 import(순환 방지). settings 는 app.config.Settings.
    """
    candles = fetch_periodic(stock_code, timeframe, start, end, session)
    if candles:
        return candles
    from app.services import kis

    fallback = kis.fetch_periodic(settings, stock_code, timeframe, start, end, session)
    if fallback:
        logger.info("chart fallback to KIS for %s/%s (%d bars)", stock_code, timeframe, len(fallback))
    return fallback


def fetch_periodic_foreign(
    symbol: str, timeframe: str, start: datetime, end: datetime, session: requests.Session
) -> list[Candle]:
    """미국 ETF/종목 봉을 조회한다(chart/foreign/item). 응답 스키마는 domestic 과 동일.

    symbol 은 네이버 RIC 접미사 포함 심볼(예: XLK, SMH.O, XLRE.K). 외국인비율은 없다.
    """
    rows = _get(
        f"{_FOREIGN_BASE}/{symbol}/{timeframe}",
        {"startDateTime": start.strftime("%Y%m%d%H%M"), "endDateTime": end.strftime("%Y%m%d%H%M")},
        session,
    )
    return _parse_periodic(rows)


def _resample_30min(minute_rows: list[dict]) -> list[Candle]:
    """네이버 1분봉(dict)을 30분봉으로 리샘플한다(OHLC 집계, 거래량 합산)."""
    minutes: list[Candle] = []
    for r in minute_rows:
        try:
            minutes.append(
                Candle(
                    ts=datetime.strptime(r["localDateTime"], "%Y%m%d%H%M%S"),
                    open=float(r["openPrice"]),
                    high=float(r["highPrice"]),
                    low=float(r["lowPrice"]),
                    close=float(r["currentPrice"]),  # 분봉 종가는 currentPrice
                    volume=int(r.get("accumulatedTradingVolume", 0)),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return resample_candles_30min(minutes)


def resample_candles_30min(minutes: list[Candle]) -> list[Candle]:
    """1분봉 Candle 리스트를 30분봉으로 리샘플한다(OHLC 집계·거래량 합산). 소스 무관 공용."""
    buckets: dict[datetime, list[Candle]] = {}
    for c in minutes:
        floored = c.ts.replace(
            minute=(c.ts.minute // _INTRADAY_BUCKET_MIN) * _INTRADAY_BUCKET_MIN, second=0
        )
        buckets.setdefault(floored, []).append(c)

    out: list[Candle] = []
    for bucket_ts in sorted(buckets):
        rows = sorted(buckets[bucket_ts], key=lambda c: c.ts)  # open/close 정확성
        out.append(
            Candle(
                ts=bucket_ts,
                open=rows[0].open,
                high=max(c.high for c in rows),
                low=min(c.low for c in rows),
                close=rows[-1].close,
                volume=sum(c.volume for c in rows),
            )
        )
    return out


def fetch_intraday_30min(stock_code: str, session: requests.Session) -> list[Candle]:
    """네이버 분봉(1분)을 받아 30분봉으로 리샘플한다. 가용 구간(최근 ~5거래일)만."""
    rows = _get(f"{_BASE}/{stock_code}/minute", {"minuteUnit": 1}, session)
    return _resample_30min(rows)
