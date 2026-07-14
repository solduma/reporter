"""전 종목 봉 적재 배치 — 10년 일봉 병렬 백필 + 매일 저녁 증분/재적재.

유니버스 스냅샷의 보통주 전 종목에 대해 네이버(→KIS 폴백) 일봉을 받아 PriceCandle 에
멱등 upsert 한다. 실패 종목은 건너뛰고 계속한다.

10년 백필(run_backfill_progressive)은 네이버 조회를 스레드풀로 병렬화해 전 종목을
하룻밤에 채운다. 조회만 병렬이고 DB 쓰기는 호출 스레드 단일 세션에서만 수행한다.

매일 저녁 배치(run_candle_batch)는 증분 수집을 기본으로 하되, 전날 종가가 저장분과
불일치하면(합병·액면분할 등 소급 조정) 해당 종목의 봉을 전체 파기 후 재적재한다.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.market import kis
from app.adapters.market import naver as chart
from app.config import Settings, get_settings
from app.db.models import PriceCandle, PriceCandleIntraday, SyncState, Timeframe, UniverseSnapshot
from app.services import candle_service, intraday, sync_state, universe_ingest
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
    as_of = universe_ingest.latest_snapshot_date(db)
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


# ── 장중 일봉 증분 갱신 (스크리너 선반영, 병렬) ─────────────────────────
# 장중 30분 사이클 전용: 전 종목의 '오늘 형성 중인 일봉'만 병렬로 증분 갱신한다. 저녁 배치와 달리
# 주식변동 판별·주봉·30분봉은 건드리지 않는다(장중 소급 조정은 없고, 추세·모멘텀 점수는 일봉만
# 필요). 오늘 봉을 price_candles 에 써두면 is_stale(오늘)=False 가 되어 상세페이지가 자체 fetch
# 를 멈추고 배치와 같은 봉을 읽는다 → 스크리너와 상세 점수가 정확히 일치한다.
_INTRADAY_DAY_WORKERS = 6  # 무인증 네이버 동시 조회(밴 방지). 6워커면 전 종목 ~2~3분.
_INTRADAY_DAY_RANGE_DAYS = 7  # 형성봉 + 직전 며칠(증분이라 최근분만)


def refresh_today_day_candles(db: Session, settings: Settings | None = None) -> dict:
    """전 유니버스의 오늘 일봉을 병렬 증분 갱신한다(장중 스크리너 선반영용).

    조회만 스레드풀로 병렬화하고 DB 쓰기는 호출 스레드 단일 세션에서만 수행한다(Session 은
    스레드 비안전 — 10년 백필과 동일 패턴). 반환: {updated, failed, total}.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip intraday day refresh")
        return {"updated": 0, "failed": 0, "total": 0}

    end = datetime.now()
    start = end - timedelta(days=_INTRADAY_DAY_RANGE_DAYS)

    def _fetch(code: str) -> tuple[str, list[chart.Candle]]:
        with requests.Session() as session:  # 스레드 간 세션 공유 금지
            return code, chart.fetch_periodic_with_fallback(settings, code, "day", start, end, session)

    updated = failed = 0
    window = _INTRADAY_DAY_WORKERS * 4  # 결과 동시 상주 제한(10년 백필과 동일)
    with ThreadPoolExecutor(max_workers=_INTRADAY_DAY_WORKERS) as pool:
        for i in range(0, len(codes), window):
            futures = {pool.submit(_fetch, c): c for c in codes[i : i + window]}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    _, candles = fut.result()
                    if candles:
                        candle_service.batch_upsert_periodic(db, code, Timeframe.DAY, candles)
                        updated += 1
                except Exception as e:  # 한 종목 실패가 사이클을 막지 않도록
                    db.rollback()
                    failed += 1
                    logger.warning("intraday day refresh failed for %s: %s", code, e)
            futures.clear()

    logger.info("intraday day refresh: updated=%d failed=%d total=%d", updated, failed, len(codes))
    return {"updated": updated, "failed": failed, "total": len(codes)}


# ── 10년 일봉 점진 백필 (야간, 재개 가능) ──────────────────────────────

_BACKFILL_DOMAIN = "backfill_10y"  # SyncState 도메인: 10년 백필 완료 종목 마킹
# 하룻밤에 처리할 종목 수. 네이버 조회를 스레드로 병렬화(_BACKFILL_WORKERS)해 전 종목(~2.8천)을
# 한 번에 커버한다. 그래도 크래시·중단 대비 sync_state 마킹으로 재개는 유지한다.
_BACKFILL_PER_RUN = 3000
# 네이버 무인증 API 동시 조회 수. 정합성 100%(단일 수정주가 소스)이나 과한 동시성은 차단
# 위험이라 보수적으로. 6워커면 종목당 ~1.7s 조회가 겹쳐 전 종목 ~13분에 완성된다.
_BACKFILL_WORKERS = 6


def _backfilled_codes(db: Session) -> set[str]:
    """이미 10년 백필 완료로 마킹된 종목코드 집합(재개 시 재처리 방지)."""
    return set(
        db.scalars(
            select(SyncState.stock_code).where(SyncState.domain == _BACKFILL_DOMAIN)
        ).all()
    )


def run_backfill_progressive(
    db: Session,
    settings: Settings | None = None,
    per_run: int = _BACKFILL_PER_RUN,
    workers: int = _BACKFILL_WORKERS,
) -> dict:
    """유니버스 종목의 일봉을 10년치로 병렬 백필한다(재개 가능).

    네이버(수정주가 단일 소스) 조회만 스레드풀로 병렬화하고, 메인 세션에 대한 DB 쓰기·
    마킹은 호출 스레드에서만 수행한다(Session 은 스레드 비안전). 완료 종목은
    SyncState(backfill_10y)로 마킹해 중단 시 다음 실행에서 이어받는다.
    반환: {done, failed, remaining}.
    """
    settings = settings or get_settings()
    codes = _universe_codes(db)
    if not codes:
        logger.warning("no universe stocks; skip 10y backfill")
        return {"done": 0, "failed": 0, "remaining": 0}

    already = _backfilled_codes(db)
    pending = [c for c in codes if c not in already]
    batch = pending[:per_run]
    end = datetime.now()
    start = end - timedelta(days=_DAY_RANGE_DAYS)  # 10년

    def _fetch(code: str) -> tuple[str, list[chart.Candle]]:
        # 조회마다 자체 requests.Session(스레드 간 공유 금지). with 로 커넥션 확실히 닫는다.
        with requests.Session() as session:
            return code, chart.fetch_periodic_with_fallback(settings, code, "day", start, end, session)

    def _store(code: str, candles: list[chart.Candle]) -> None:
        # 봉당 execute(느림) 대신 단일 다중값 INSERT(candle_service)로 배치 upsert(~7배 빠름).
        # 진짜 병목은 조회가 아니라 종목당 수천 봉 적재이므로 여기 배치화가 핵심.
        if candles:
            candle_service.batch_upsert_periodic(db, code, Timeframe.DAY, candles)  # 자체 commit
        sync_state.mark(db, _BACKFILL_DOMAIN, code)
        db.commit()  # 멱등이라 upsert 후 크래시로 마킹 누락돼도 다음 실행 재적재는 무해

    done = failed = 0
    # future 는 result 를 소비 후에도 붙들고 dict 도 풀 수명 내내 참조를 유지하므로, 전 종목
    # future 를 한 번에 제출하면 수천 종목x수천 봉이 동시 상주해 메모리가 폭증한다. 워커의 몇 배
    # 크기 윈도우로 제출→소비→참조 해제를 반복해 동시 상주 결과를 제한한다.
    window = workers * 4
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(batch), window):
            futures = {pool.submit(_fetch, c): c for c in batch[i : i + window]}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    _, candles = fut.result()
                    if not candles:
                        # 빈 응답: 일시 스로틀(6-way 병렬로 확률↑)과 진짜 데이터 없음(신규상장)을
                        # 구분하려 소비 스레드에서 1회 순차 재조회. 그래도 비면 진짜 없음으로 마킹.
                        _, candles = _fetch(code)
                        if not candles:
                            logger.info("10y backfill empty (marking done): %s", code)
                    _store(code, candles)
                    done += 1
                except Exception as e:  # 한 종목 실패가 배치를 막지 않도록(다음 실행 재시도)
                    db.rollback()
                    failed += 1
                    logger.warning("10y backfill failed for %s: %s", code, e)
                if done % 200 == 0 and done:
                    logger.info("10y backfill progress: done=%d failed=%d", done, failed)
            futures.clear()  # 이 윈도우 future/result 참조 해제(다음 윈도우 전 GC 가능)

    remaining = len(pending) - done
    logger.info("10y backfill: done=%d failed=%d remaining=%d", done, failed, remaining)
    return {"done": done, "failed": failed, "remaining": remaining}
