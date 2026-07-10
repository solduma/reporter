"""봉차트 조회·갱신 — DB 우선(캐시) + 백그라운드 증분 적재.

기존 라우터는 매 요청마다 네이버를 먼저 호출하고 받은 전량을 개별 execute 로 재저장해
요청당 ~1.7초가 걸렸다(upsert 루프만 ~525ms). 이 모듈은:
- 조회: DB 만 읽어 즉시 반환(외부 0). ~30ms.
- 증분: DB 최신 bar 가 뒤처졌을 때만, 마지막 bar 이후만 조회·배치 upsert. BackgroundTask 로
  응답을 막지 않고 수행한다. 프론트는 완료분을 다음 조회에서 받는다(stale-while-revalidate).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import PriceCandle, PriceCandleIntraday, Timeframe
from app.db.session import SessionLocal
from app.services import chart, intraday

logger = logging.getLogger(__name__)

# tf 별 최초(DB 비었을 때) 조회 범위. 일=2년·주=10년·월=3년(라우터와 통일).
RANGE_DAYS = {"day": 365 * 2 + 10, "week": 365 * 10 + 30, "month": 365 * 3 + 30}

# 같은 (code, tf) 증분 갱신이 동시에 여러 번 돌지 않도록 하는 인프로세스 가드
# (한 페이지가 차트 6~7개를 동시에 열어 같은 심볼을 중복 트리거하는 것을 막는다).
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

# 갱신 쿨다운 — 마감 후·주말·공휴일엔 새 봉이 없어 is_stale 이 계속 True 다. 쿨다운 없이는
# 매 요청이 헛된 외부 조회를 유발하므로(rate-limit 위험), 같은 심볼은 이 간격 내 재조회를 막는다.
_REFRESH_COOLDOWN_S = 600.0  # 10분
_last_attempt: dict[str, float] = {}
_attempt_lock = threading.Lock()


def _cooldown_ok(key: str) -> bool:
    """마지막 시도 후 쿨다운이 지났으면 True 를 주고 시도 시각을 갱신한다(동시성 안전)."""
    now = time.monotonic()
    with _attempt_lock:
        last = _last_attempt.get(key)
        if last is not None and now - last < _REFRESH_COOLDOWN_S:
            return False
        _last_attempt[key] = now
        return True


def read_periodic(db: Session, code: str, tf: str) -> list[PriceCandle]:
    """저장된 일/주/월봉을 날짜 오름차순으로 반환한다(외부 호출 없음)."""
    return list(
        db.scalars(
            select(PriceCandle)
            .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe(tf))
            .order_by(PriceCandle.bar_date)
        ).all()
    )


def read_intraday(db: Session, code: str, days: int = 14) -> list[PriceCandleIntraday]:
    """저장된 30분봉 최근 days 일치를 시각 오름차순으로 반환한다(외부 호출 없음)."""
    window_start = datetime.now() - timedelta(days=days)
    return list(
        db.scalars(
            select(PriceCandleIntraday)
            .where(
                PriceCandleIntraday.stock_code == code,
                PriceCandleIntraday.bar_ts >= window_start,
            )
            .order_by(PriceCandleIntraday.bar_ts)
        ).all()
    )


def _latest_bar_date(db: Session, code: str, tf: Timeframe):
    return db.scalar(
        select(func.max(PriceCandle.bar_date)).where(
            PriceCandle.stock_code == code, PriceCandle.timeframe == tf
        )
    )


def batch_upsert_periodic(
    db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]
) -> int:
    """봉들을 단일 다중행 INSERT ... ON CONFLICT 로 upsert 한다(개별 execute 루프 제거).

    반환값은 입력 봉 수(신규/갱신 구분 없이). 빈 입력이면 0.
    """
    if not candles:
        return 0
    # 같은 bar_date 가 중복되면 다중행 ON CONFLICT 가 "cannot affect row a second time"(21000)로
    # 실패한다. 소스가 드물게 같은 날짜를 두 번 줄 수 있으므로 날짜별 마지막 값만 남긴다.
    by_date: dict = {}
    for c in candles:
        by_date[c.ts.date()] = c
    rows = [
        {
            "stock_code": code,
            "timeframe": tf,
            "bar_date": d,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "foreign_ratio": c.foreign_ratio,
        }
        for d, c in by_date.items()
    ]
    stmt = insert(PriceCandle).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_candle",
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "foreign_ratio": stmt.excluded.foreign_ratio,
        },
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


def ensure_periodic(db: Session, code: str, tf: str) -> list[PriceCandle]:
    """DB 우선. 비어 있으면 그때만 동기 조회(최초 1회), 아니면 저장분을 즉시 반환한다.

    호출측이 BackgroundTask 로 refresh_periodic 을 걸어 증분 최신화를 별도로 수행한다.
    """
    rows = read_periodic(db, code, tf)
    if rows:
        return rows
    # DB 가 비었으면(처음 보는 심볼) 최초 1회 동기 조회로 화면을 채운다. 단 데이터가 아예 없는
    # 심볼(상폐·오타)은 매 요청 ~1.7s 동기 조회를 반복하지 않도록 쿨다운으로 막는다.
    if _cooldown_ok(f"{code}|{tf}"):
        _fetch_and_store(db, code, tf, since=None)
        rows = read_periodic(db, code, tf)
    return rows


def is_stale(db: Session, code: str, tf: str) -> bool:
    """저장분 최신 bar 가 오늘보다 뒤처져 증분 갱신이 필요한지."""
    latest = _latest_bar_date(db, code, Timeframe(tf))
    return latest is None or latest < datetime.now().date()


def _fetch_and_store(db: Session, code: str, tf: str, since) -> int:
    """네이버(→KIS 폴백)로 [since→now] 구간(since 없으면 전체 범위)을 조회·배치 upsert."""
    session = requests.Session()
    end = datetime.now()
    # since 가 있으면 마지막 봉 하루 전부터(경계 봉 갱신 포함), 없으면 tf 기본 범위 전체.
    start = (
        datetime(since.year, since.month, since.day) - timedelta(days=1)
        if since
        else end - timedelta(days=RANGE_DAYS[tf])
    )
    fresh = chart.fetch_periodic_with_fallback(get_settings(), code, tf, start, end, session)
    return batch_upsert_periodic(db, code, Timeframe(tf), fresh)


def refresh_periodic(code: str, tf: str) -> None:
    """백그라운드 증분 갱신 — 자체 세션. 마지막 bar 이후만 조회·적재한다.

    같은 (code, tf) 가 이미 갱신 중이거나 쿨다운 내면 건너뛴다(중복·헛된 외부 호출 방지).
    예외는 흡수한다(백그라운드라 요청 흐름에 영향 없음).
    """
    key = f"{code}|{tf}"
    if not _cooldown_ok(key):
        return
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)
    try:
        db = SessionLocal()
        try:
            latest = _latest_bar_date(db, code, Timeframe(tf))
            n = _fetch_and_store(db, code, tf, since=latest)
            logger.info("candle refresh %s: +%d bars (since=%s)", key, n, latest)
        finally:
            db.close()
    except Exception as e:  # 백그라운드 갱신 실패가 조회를 깨지 않도록
        logger.warning("candle refresh failed %s: %s", key, e)
    finally:
        with _inflight_lock:
            _inflight.discard(key)


def read_intraday_or_fetch(db: Session, code: str, days: int = 14) -> list[PriceCandleIntraday]:
    """DB 우선 30분봉. 비었으면(cron·백필 미커버 종목) 최초 1회 동기 조회로 채운다.

    이후 최신화는 refresh_intraday(백그라운드)가 담당. 첫 로드 빈 화면(회귀) 방지.
    """
    rows = read_intraday(db, code, days)
    if rows:
        return rows
    session = requests.Session()
    fresh = chart.fetch_intraday_30min(code, session)
    if fresh:
        intraday.upsert_intraday(db, code, fresh)
    return read_intraday(db, code, days)


def refresh_intraday(code: str) -> None:
    """백그라운드 30분봉 갱신 — 자체 세션. 가용 분봉 리샘플·누적."""
    key = f"{code}|30m"
    if not _cooldown_ok(key):
        return
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)
    try:
        db = SessionLocal()
        try:
            session = requests.Session()
            fresh = chart.fetch_intraday_30min(code, session)
            if fresh:
                intraday.upsert_intraday(db, code, fresh)
        finally:
            db.close()
    except Exception as e:
        logger.warning("intraday refresh failed %s: %s", key, e)
    finally:
        with _inflight_lock:
            _inflight.discard(key)
