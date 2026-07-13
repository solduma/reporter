"""IBD RS Rating + 기술적 추세 배치 — 전 유니버스의 가격지표를 price_candles 로 사전계산.

price_candles(백필 완료)만 읽어 계산하므로 외부 fetch 가 없다(야간 배치). 종목별로:
- RS Rating: 강도지수를 구해 전 종목 횡단면 백분위(1~99)로 매긴다.
- trend_score: 종목분석과 동일한 4요소(신고가 근접·이평 정배열·거래량비·3개월 수익률) 종합 0~100.
둘 다 같은 OHLCV 를 한 번 읽어 계산하고 universe_snapshot 에 UPDATE 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PriceCandle, Timeframe, UniverseSnapshot
from app.domain import rs_rating, stage, technicals
from app.services import universe_ingest

logger = logging.getLogger(__name__)

# 강도지수(1년)·이평 정배열(120일) 계산에 필요한 봉보다 여유롭게 최근분만 읽어 메모리·시간 절약.
_LOOKBACK_BARS = 300


@dataclass
class _Bar:
    """technicals.compute 가 요구하는 봉 인터페이스(close/high/low/volume) + 리샘플용 날짜."""

    close: float
    high: float
    low: float
    volume: int
    bar_date: str = ""


def _mid_stage(bars: list[_Bar]) -> int | None:
    """일봉 → 주봉 리샘플 후 와인스타인 중기 국면(주봉 MA30) 판정. 추세 점수 보조 가중용."""
    if not bars or not bars[0].bar_date:
        return None
    fr = stage.FRAMES["mid"]
    b = stage.resample_ohlcv(
        [x.bar_date for x in bars],
        [x.high for x in bars],
        [x.low for x in bars],
        [x.close for x in bars],
        [x.volume for x in bars],
        fr.bar,
    )
    if len(b.closes) < fr.ma_period:
        return None
    return stage.classify(
        b.closes, fr.ma_period, fr.slope_lookback, b.volumes, b.highs, b.lows
    ).stage


def _universe_codes(db: Session, snap_date: date) -> list[str]:
    stmt = select(UniverseSnapshot.stock_code).where(
        UniverseSnapshot.snapshot_date == snap_date,
        UniverseSnapshot.stock_type == "stock",
        UniverseSnapshot.market_cap.is_not(None),
    )
    return list(db.scalars(stmt).all())


def _recent_bars(db: Session, code: str) -> list[_Bar]:
    """종목의 최근 일봉 OHLCV(오름차순). RS·추세 계산에 필요한 만큼만 읽는다."""
    rows = db.execute(
        select(
            PriceCandle.close, PriceCandle.high, PriceCandle.low,
            PriceCandle.volume, PriceCandle.bar_date,
        )
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(_LOOKBACK_BARS)
    ).all()
    return [_Bar(r[0], r[1], r[2], int(r[3] or 0), r[4].isoformat()) for r in reversed(rows)]


def run_rs_rating_batch(db: Session) -> dict:
    """전 유니버스의 RS Rating(1~99)·추세 점수(0~100)를 계산·적재한다. 처리 종목 수를 반환한다."""
    snap_date = universe_ingest.latest_snapshot_date(db)
    if not snap_date:
        return {"rated": 0, "total": 0}

    codes = _universe_codes(db, snap_date)
    # 1) 종목별 OHLCV 1회 read → 강도지수 + 추세 점수 계산(외부 fetch 없음).
    factors: dict[str, float] = {}
    trend_scores: dict[str, float] = {}
    for code in codes:
        bars = _recent_bars(db, code)
        closes = [b.close for b in bars]
        sf = rs_rating.strength_factor(closes)
        if sf is not None:
            factors[code] = sf
        # 추세 점수에 와인스타인 중기 국면(주봉)을 보조 가중으로 반영 — 종목분석과 동일.
        ts = technicals.compute(bars, stage=_mid_stage(bars)).trend_score
        if ts is not None:
            trend_scores[code] = ts

    # 2) 전 종목 횡단면 백분위 → RS 1~99. 추세 점수는 절대값이라 그대로 적재.
    sorted_factors = sorted(factors.values())
    rated = 0
    for code in codes:
        values: dict = {}
        sf = factors.get(code)
        if sf is not None:
            rating = rs_rating.to_rating(sf, sorted_factors)
            if rating is not None:
                values["rs_rating"] = rating
        if code in trend_scores:
            values["trend_score"] = trend_scores[code]
        if not values:
            continue
        db.execute(
            UniverseSnapshot.__table__.update()
            .where(
                UniverseSnapshot.snapshot_date == snap_date,
                UniverseSnapshot.stock_code == code,
            )
            .values(**values)
        )
        rated += 1
    db.commit()
    logger.info(
        "rs+trend batch: %d/%d updated (rs=%d trend=%d) (%s)",
        rated, len(codes), len(factors), len(trend_scores), snap_date,
    )
    return {"rated": rated, "total": len(codes), "trend": len(trend_scores)}
