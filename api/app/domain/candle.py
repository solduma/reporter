"""봉(Candle) 값 객체 + 순수 리샘플 — 도메인 코어.

소스(네이버·KIS·…)와 무관한 봉 표현과 30분 리샘플 규칙. 외부 IO·프레임워크를 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
