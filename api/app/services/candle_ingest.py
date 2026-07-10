"""전 종목 봉 적재 배치 — 일봉 2년치 백필 + 매일 저녁 증분/재적재.

유니버스 스냅샷의 보통주 전 종목에 대해 네이버(→KIS 폴백) 일봉을 받아 PriceCandle 에
멱등 upsert 한다. 종목당 ~0.1s 라 ~2800종목이면 수 분. 실패 종목은 건너뛰고 계속한다.

매일 저녁 배치(run_candle_batch)는 증분 수집을 기본으로 하되, 전날 종가가 저장분과
불일치하면(합병·액면분할 등 소급 조정) 해당 종목의 봉을 전체 파기 후 재적재한다.
"""

from __future__ import annotations

import logging
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

_DAY_RANGE_DAYS = 365 * 2 + 10  # 2년치
_WEEK_RANGE_DAYS = 365 * 10 + 30  # 10년치(주봉)
_INTRADAY_TRADING_DAYS = 10  # 2주 ≈ 거래일 10일
# tf → 재적재 시 조회 범위(일수). 30분봉은 KIS 거래일 기반이라 별도 처리.
_PERIODIC_RANGE = {Timeframe.DAY: _DAY_RANGE_DAYS, Timeframe.WEEK: _WEEK_RANGE_DAYS}


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


def _refresh_one_periodic(
    db: Session, settings: Settings, code: str, tf: Timeframe, session: requests.Session
) -> str:
    """한 종목·tf 를 증분 갱신하되, 전날 종가 불일치면 전체 재적재한다.

    반환: "reload"(재적재) | "incremental"(증분) | "seed"(최초 적재) | "noop".
    """
    stored = _last_two_periodic(db, code, tf)
    if not stored:
        # 최초 적재(주봉 미백필 종목 등) — 전체 범위 조회.
        fresh = _fetch_periodic_range(settings, code, tf, _PERIODIC_RANGE[tf], session)
        _upsert(db, code, tf, fresh)
        db.commit()
        return "seed"

    # 마지막 bar 이후만 좁게 조회하되, 전날 종가 대조를 위해 직전 확정 bar 까지 포함해 받는다.
    since = stored[-2].bar_date if len(stored) >= 2 else stored[-1].bar_date
    end = datetime.now()
    start = datetime(since.year, since.month, since.day) - timedelta(days=3)
    fresh = chart.fetch_periodic_with_fallback(settings, code, tf.value, start, end, session)
    if not fresh:
        return "noop"

    if _corporate_action(stored, fresh):
        log_fallback(
            "candle.corporate_action_reload",
            reason=f"전날 종가 불일치(합병·분할 등 소급 조정) → 전체 재적재 {tf.value}",
            detail=code,
        )
        _delete_all_candles(db, code)
        full = _fetch_periodic_range(settings, code, tf, _PERIODIC_RANGE[tf], session)
        _upsert(db, code, tf, full)
        db.commit()
        return "reload"

    _upsert(db, code, tf, fresh)
    db.commit()
    return "incremental"


def run_candle_batch(db: Session, settings: Settings | None = None) -> dict:
    """매일 저녁: 유니버스 전 종목의 일·주·30분봉을 증분 갱신(변동 시 전체 재적재).

    종가 불일치가 감지되면 그 종목의 모든 봉을 파기 후 재적재하므로, 이후 tf 처리는
    자연히 seed 경로로 다시 채운다. 실패 종목은 건너뛴다.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip candle batch")
        return {"stocks": 0, "reloaded": 0, "failed": 0}

    session = requests.Session()
    stats = {"stocks": 0, "reloaded": 0, "incremental": 0, "seed": 0, "failed": 0}
    reloaded_codes: set[str] = set()
    for i, code in enumerate(codes, 1):
        try:
            for tf in (Timeframe.DAY, Timeframe.WEEK):
                # 앞선 tf 에서 이미 재적재(전체 파기)했으면 이 tf 는 seed 로 채워진다.
                outcome = _refresh_one_periodic(db, settings, code, tf, session)
                if outcome == "reload":
                    reloaded_codes.add(code)
                stats[outcome] = stats.get(outcome, 0) + 1
            stats["stocks"] += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            stats["failed"] += 1
            logger.warning("candle batch failed for %s: %s", code, e)
        if i % 200 == 0:
            logger.info("candle batch %d/%d (reloaded=%d)", i, len(codes), len(reloaded_codes))

    # 30분봉: 유니버스 전 종목을 네이버 분봉(1콜/종목)으로 누적한다. 네이버는 최근 ~5거래일만
    # 주므로 이 배치가 2주 윈도우의 최근 구간을 유지한다(과거 구간은 KIS backfill_intraday 담당).
    # 재적재(전체 파기)된 종목도 여기서 최근 30분봉이 다시 채워진다.
    intraday_touched = 0
    for code in codes:
        try:
            bars = chart.fetch_intraday_30min(code, session)
            if bars:
                intraday.upsert_intraday(db, code, bars)
                intraday_touched += 1
        except Exception as e:
            db.rollback()
            logger.warning("candle batch intraday failed for %s: %s", code, e)
    stats["reloaded"] = len(reloaded_codes)
    stats["intraday_touched"] = intraday_touched
    logger.info("candle batch done: %s", stats)
    return stats
