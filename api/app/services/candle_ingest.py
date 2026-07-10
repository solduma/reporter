"""전 종목 봉 적재 배치 — 일봉 2년치 백필 + 매일 저녁 증분/재적재.

유니버스 스냅샷의 보통주 전 종목에 대해 네이버(→KIS 폴백) 일봉을 받아 PriceCandle 에
멱등 upsert 한다. 종목당 ~0.1s 라 ~2800종목이면 수 분. 실패 종목은 건너뛰고 계속한다.

매일 저녁 배치(run_candle_batch)는 증분 수집을 기본으로 하되, 전날 종가가 저장분과
불일치하면(합병·액면분할 등 소급 조정) 해당 종목의 봉을 전체 파기 후 재적재한다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import requests
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import PriceCandle, PriceCandleIntraday, Timeframe, UniverseSnapshot
from app.services import chart, intraday, kis
from reporter.fallback import log_fallback

logger = logging.getLogger(__name__)

_DAY_RANGE_DAYS = 365 * 10 + 30  # 10년치
_WEEK_RANGE_DAYS = 365 * 10 + 30  # 10년치(주봉)
_INTRADAY_TRADING_DAYS = 10  # 2주 ≈ 거래일 10일
# tf → 재적재 시 조회 범위(일수). 30분봉은 KIS 거래일 기반이라 별도 처리.
_PERIODIC_RANGE = {Timeframe.DAY: _DAY_RANGE_DAYS, Timeframe.WEEK: _WEEK_RANGE_DAYS}
# 저녁 배치는 종목당 여러 네이버 콜(일·주·30분)을 낸다. 무인증 API 를 초당 수십 콜로 연타하면
# 차단 위험이 있어 종목 간 짧은 간격을 둔다(~2653종목 x 0.15s = 약 7분 추가, 야간이라 무해).
_BATCH_STOCK_INTERVAL_S = 0.15


def _universe_codes(db: Session) -> list[str]:
    """최신 스냅샷의 보통주 종목코드(ETF/ETN·우선주 제외)."""
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    if as_of is None:
        return []
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
        ).all()
    )


def _upsert(db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandle).values(
            stock_code=code, timeframe=tf, bar_date=c.ts.date(),
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            foreign_ratio=c.foreign_ratio,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle",
            set_={"open": stmt.excluded.open, "high": stmt.excluded.high,
                  "low": stmt.excluded.low, "close": stmt.excluded.close,
                  "volume": stmt.excluded.volume, "foreign_ratio": stmt.excluded.foreign_ratio},
        )
        db.execute(stmt)


def backfill_daily(db: Session, settings: Settings | None = None) -> dict:
    """전 종목 일봉 2년치를 적재한다. {'stocks': 처리수, 'failed': 실패수}."""
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip daily backfill")
        return {"stocks": 0, "failed": 0}

    session = requests.Session()
    end = datetime.now()
    start = end - timedelta(days=_DAY_RANGE_DAYS)
    done = failed = 0
    for i, code in enumerate(codes, 1):
        try:
            candles = chart.fetch_periodic_with_fallback(settings, code, "day", start, end, session)
            if candles:
                _upsert(db, code, Timeframe.DAY, candles)
                db.commit()  # 종목 단위 커밋 — 중간 중단해도 앞선 종목 보존
                done += 1
            else:
                failed += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("daily backfill failed for %s: %s", code, e)
        if i % 200 == 0:
            logger.info("daily backfill %d/%d (ok=%d fail=%d)", i, len(codes), done, failed)

    logger.info("daily backfill done: %d stocks, %d failed", done, failed)
    return {"stocks": done, "failed": failed}


def _recent_trading_days(db: Session, n: int) -> list[str]:
    """적재된 일봉의 최근 n 거래일(YYYYMMDD). 삼성전자(005930) 기준 — 장 열린 날 정확."""
    rows = db.scalars(
        select(PriceCandle.bar_date)
        .where(PriceCandle.stock_code == "005930", PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(n)
    ).all()
    return [d.strftime("%Y%m%d") for d in reversed(rows)]


def _intraday_loaded_codes(db: Session) -> set[str]:
    """이미 30분봉이 적재된 종목코드 집합 — 중단 후 재개 시 재조회 방지."""
    return set(db.scalars(select(PriceCandleIntraday.stock_code).distinct()).all())


def backfill_intraday(db: Session, settings: Settings | None = None) -> dict:
    """전 종목 30분봉 2주치(≈10거래일)를 KIS 분봉으로 적재한다. 매우 무겁다(종목당 40+콜).

    이미 적재된 종목은 건너뛰어(재개 가능) 중단 후 재실행해도 KIS 콜을 낭비하지 않는다.
    {'stocks': 처리수, 'failed': 실패수, 'skipped': 기적재수, 'days': 거래일수}.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    days = _recent_trading_days(db, _INTRADAY_TRADING_DAYS)
    if not codes or not days:
        logger.warning("no codes/days; skip intraday backfill (일봉 먼저 적재 필요)")
        return {"stocks": 0, "failed": 0, "skipped": 0, "days": len(days)}

    loaded = _intraday_loaded_codes(db)
    pending = [c for c in codes if c not in loaded]
    session = requests.Session()
    done = failed = 0
    for i, code in enumerate(pending, 1):
        try:
            bars = kis.fetch_intraday_30min(settings, code, days, session)
            if bars:
                intraday.upsert_intraday(db, code, bars)
                done += 1
            else:
                failed += 1
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("intraday backfill failed for %s: %s", code, e)
        if i % 100 == 0:
            logger.info(
                "intraday backfill %d/%d (ok=%d fail=%d, skipped=%d)",
                i, len(pending), done, failed, len(loaded),
            )

    logger.info(
        "intraday backfill done: %d stocks, %d failed, %d skipped, %d days",
        done, failed, len(loaded), len(days),
    )
    return {"stocks": done, "failed": failed, "skipped": len(loaded), "days": len(days)}


# ── 매일 저녁 배치: 증분 + 주식변동(합병 등) 감지 재적재 ──────────────────────────

# 종가 비교 허용 오차 — 부동소수 저장 오차만 흡수(실질 조정은 이보다 훨씬 큼).
_CLOSE_EPS = 0.01


def _last_two_periodic(db: Session, code: str, tf: Timeframe) -> list[PriceCandle]:
    """저장된 일/주봉 중 최근 2개(오름차순). 전날 종가 대조·마지막 bar 파악용."""
    rows = db.scalars(
        select(PriceCandle)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == tf)
        .order_by(PriceCandle.bar_date.desc())
        .limit(2)
    ).all()
    return list(reversed(rows))


def _corporate_action(stored: list[PriceCandle], fresh: list[chart.Candle]) -> bool:
    """저장분과 새 조회분에서 '직전 확정 bar'의 종가가 불일치하면 주식변동으로 본다.

    합병·액면분할·유상증자 등은 과거 가격이 소급 조정되므로, 마지막 저장 bar 이전의
    확정 bar(오늘 미확정 bar 제외를 위해 마지막에서 하나 앞) 종가를 대조한다.
    저장분이 1개 이하면 대조 불가 → False(증분으로 처리).
    """
    if len(stored) < 2 or not fresh:
        return False
    ref = stored[-2]  # 직전 확정 bar(마지막은 당일 진행 중일 수 있어 제외)
    fresh_by_date = {c.ts.date(): c for c in fresh}
    match = fresh_by_date.get(ref.bar_date)
    if match is None:
        return False  # 새 조회에 그 날짜가 없으면 판단 보류(증분)
    return abs(match.close - ref.close) > _CLOSE_EPS


def _delete_all_candles(db: Session, code: str) -> None:
    """한 종목의 일/주/30분봉을 전부 삭제한다(주식변동 재적재 전 파기)."""
    db.execute(delete(PriceCandle).where(PriceCandle.stock_code == code))
    db.execute(delete(PriceCandleIntraday).where(PriceCandleIntraday.stock_code == code))
    db.commit()


def _fetch_periodic_range(
    settings: Settings, code: str, tf: Timeframe, days: int, session: requests.Session
) -> list[chart.Candle]:
    end = datetime.now()
    start = end - timedelta(days=days)
    return chart.fetch_periodic_with_fallback(settings, code, tf.value, start, end, session)



def _seed_or_incremental(
    db: Session, settings: Settings, code: str, tf: Timeframe, session: requests.Session
) -> str:
    """한 종목·tf 를 채운다: 저장분 없으면 seed(전체), 있으면 incremental(마지막 이후만).

    주식변동 감지·재적재는 호출측(run_candle_batch)이 종목 단위로 판단한다.
    반환: "seed" | "incremental" | "noop".
    """
    stored = _last_two_periodic(db, code, tf)
    if not stored:
        fresh = _fetch_periodic_range(settings, code, tf, _PERIODIC_RANGE[tf], session)
        _upsert(db, code, tf, fresh)
        db.commit()
        return "seed"

    since = stored[-2].bar_date if len(stored) >= 2 else stored[-1].bar_date
    end = datetime.now()
    start = datetime(since.year, since.month, since.day) - timedelta(days=3)
    fresh = chart.fetch_periodic_with_fallback(settings, code, tf.value, start, end, session)
    if not fresh:
        return "noop"
    _upsert(db, code, tf, fresh)
    db.commit()
    return "incremental"


def _detect_corporate_action(
    db: Session, settings: Settings, code: str, session: requests.Session
) -> bool:
    """일봉 직전 확정 bar 종가로 주식변동(합병·분할 등)을 판별한다.

    일봉은 주식변동 시 전 구간이 소급 조정되므로 단일 신호로 충분하다(주봉은 라벨 편차로
    누락 가능해 보조로만). 저장 일봉이 2개 미만이면 판단 보류(False).
    """
    stored = _last_two_periodic(db, code, Timeframe.DAY)
    if len(stored) < 2:
        return False
    ref = stored[-2]
    start = datetime(ref.bar_date.year, ref.bar_date.month, ref.bar_date.day) - timedelta(days=3)
    fresh = chart.fetch_periodic_with_fallback(
        settings, code, Timeframe.DAY.value, start, datetime.now(), session
    )
    return _corporate_action(stored, fresh)


def _reload_stock(db: Session, settings: Settings, code: str, session: requests.Session) -> None:
    """한 종목의 모든 봉(일/주/30분)을 파기 후 보유기간 전체 재적재한다(주식변동 대응)."""
    _delete_all_candles(db, code)
    for tf in (Timeframe.DAY, Timeframe.WEEK):
        full = _fetch_periodic_range(settings, code, tf, _PERIODIC_RANGE[tf], session)
        _upsert(db, code, tf, full)
    db.commit()
    # 30분봉은 네이버 최근분만 우선 복원(2주 과거 구간은 별도 KIS 백필). 실패해도 무시.
    try:
        bars = chart.fetch_intraday_30min(code, session)
        if bars:
            intraday.upsert_intraday(db, code, bars)
    except Exception as e:
        logger.warning("reload intraday failed for %s: %s", code, e)


def run_candle_batch(db: Session, settings: Settings | None = None) -> dict:
    """매일 저녁: 유니버스 전 종목의 일·주·30분봉을 증분 갱신(주식변동 시 전체 재적재).

    종목 단위로 먼저 주식변동(일봉 종가 소급 조정)을 판별해, 감지되면 그 종목의 모든 봉을
    파기 후 재적재하고, 아니면 각 tf 를 seed/incremental 로 채운다. 일봉을 단일 신호로 쓰는
    이유는 주식변동이 전 구간을 소급 조정하기 때문(주봉은 라벨 편차로 보조). 실패 종목은 건너뛴다.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip candle batch")
        return {"stocks": 0, "reloaded": 0, "failed": 0}

    session = requests.Session()
    stats = {"stocks": 0, "reloaded": 0, "incremental": 0, "seed": 0, "noop": 0, "failed": 0}
    for i, code in enumerate(codes, 1):
        try:
            if _detect_corporate_action(db, settings, code, session):
                log_fallback(
                    "candle.corporate_action_reload",
                    reason="일봉 전날 종가 불일치(합병·분할 등 소급 조정) → 전체 재적재",
                    detail=code,
                )
                _reload_stock(db, settings, code, session)
                stats["reloaded"] += 1
            else:
                for tf in (Timeframe.DAY, Timeframe.WEEK):
                    outcome = _seed_or_incremental(db, settings, code, tf, session)
                    stats[outcome] = stats.get(outcome, 0) + 1
                # 30분봉: 네이버 분봉(1콜/종목)으로 최근분 누적(2주 과거는 KIS 백필 담당).
                try:
                    bars = chart.fetch_intraday_30min(code, session)
                    if bars:
                        intraday.upsert_intraday(db, code, bars)
                except Exception as e:
                    db.rollback()
                    logger.warning("candle batch intraday failed for %s: %s", code, e)
            stats["stocks"] += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            stats["failed"] += 1
            logger.warning("candle batch failed for %s: %s", code, e)
        if i % 200 == 0:
            logger.info("candle batch %d/%d (reloaded=%d)", i, len(codes), stats["reloaded"])
        time.sleep(_BATCH_STOCK_INTERVAL_S)  # 네이버 무인증 API 연타 방지

    logger.info("candle batch done: %s", stats)
    return stats
