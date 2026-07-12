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

from sqlalchemy.orm import Session

from app.adapters.market import get_market_data
from app.adapters.market import naver as chart
from app.adapters.persistence import SqlCandleRepository
from app.db.models import PriceCandle, PriceCandleIntraday, Timeframe
from app.db.session import SessionLocal
from app.ports.market_data import MarketDataPort
from app.ports.repositories import CandleRepository
from app.services import intraday

logger = logging.getLogger(__name__)

# 포트 공급자 seam — 기본은 실제 어댑터를 주지만, 테스트가 이 훅을 교체해 fake 를 주입할 수 있다
# (모든 호출부는 이 두 함수만 거치므로 시그니처 변경 없이 포트 치환이 가능하다).
def _candle_repo(db: Session) -> CandleRepository:
    return SqlCandleRepository(db)


def _market_data(market: str) -> MarketDataPort:
    return get_market_data(market)

# tf 별 최초(DB 비었을 때) 조회 범위. 일=2년·주=10년·월=3년(라우터와 통일).
RANGE_DAYS = {"day": 365 * 10 + 30, "week": 365 * 10 + 30, "month": 365 * 10 + 30}

# 같은 (code, tf) 증분 갱신이 동시에 여러 번 돌지 않도록 하는 인프로세스 가드
# (한 페이지가 차트 6~7개를 동시에 열어 같은 심볼을 중복 트리거하는 것을 막는다).
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

# 갱신 쿨다운 — 마감 후·주말·공휴일엔 새 봉이 없어 is_stale 이 계속 True 다. 쿨다운 없이는
# 매 요청이 헛된 외부 조회를 유발하므로(rate-limit 위험), 같은 심볼은 이 간격 내 재조회를 막는다.
_REFRESH_COOLDOWN_S = 600.0  # 10분(일/주/월봉 — 하루 1회만 새 봉)
# 30분봉은 장중 형성 중인 봉의 고가·저가·종가가 계속 바뀌므로 훨씬 짧게 둔다(의사 실시간).
_INTRADAY_COOLDOWN_S = 60.0
_last_attempt: dict[str, float] = {}
_attempt_lock = threading.Lock()


def _cooldown_ok(key: str, cooldown: float = _REFRESH_COOLDOWN_S) -> bool:
    """마지막 시도 후 쿨다운이 지났으면 True 를 주고 시도 시각을 갱신한다(동시성 안전)."""
    now = time.monotonic()
    with _attempt_lock:
        last = _last_attempt.get(key)
        if last is not None and now - last < cooldown:
            return False
        _last_attempt[key] = now
        return True


def read_periodic(db: Session, code: str, tf: str) -> list[PriceCandle]:
    """저장된 일/주/월봉을 날짜 오름차순으로 반환한다(외부 호출 없음)."""
    return _candle_repo(db).read_periodic(code, tf)


def read_intraday(db: Session, code: str, days: int = 14) -> list[PriceCandleIntraday]:
    """저장된 30분봉 최근 days 일치를 시각 오름차순으로 반환한다(외부 호출 없음)."""
    return _candle_repo(db).read_intraday(code, days)


def _latest_bar_date(db: Session, code: str, tf: Timeframe):
    return _candle_repo(db).latest_bar_date(code, tf)


def batch_upsert_periodic(
    db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]
) -> int:
    """봉들을 upsert 한다(영속화는 CandleRepository 에 위임). 반영 건수 반환, 빈 입력이면 0."""
    return _candle_repo(db).upsert_periodic(code, tf, candles)


def ensure_periodic(db: Session, code: str, tf: str, market: str = "KR") -> list[PriceCandle]:
    """DB 우선. 비어 있으면 그때만 동기 조회(최초 1회), 아니면 저장분을 즉시 반환한다.

    호출측이 BackgroundTask 로 refresh_periodic 을 걸어 증분 최신화를 별도로 수행한다.
    국내/미국 공용(market 로 소스 분기).
    """
    rows = read_periodic(db, code, tf)
    if rows:
        return rows
    # DB 가 비었으면(처음 보는 심볼) 최초 1회 동기 조회로 화면을 채운다. 단 데이터가 아예 없는
    # 심볼(상폐·오타)은 매 요청 ~1.7s 동기 조회를 반복하지 않도록 쿨다운으로 막는다.
    if _cooldown_ok(f"{code}|{tf}"):
        _fetch_and_store(db, code, tf, since=None, market=market)
        rows = read_periodic(db, code, tf)
    return rows


def is_stale(db: Session, code: str, tf: str) -> bool:
    """저장분 최신 bar 가 오늘보다 뒤처져 증분 갱신이 필요한지."""
    latest = _latest_bar_date(db, code, Timeframe(tf))
    return latest is None or latest < datetime.now().date()


def _fetch_and_store(db: Session, code: str, tf: str, since, market: str = "KR") -> int:
    """[since→now] 구간(since 없으면 tf 기본 범위)을 조회·배치 upsert. 시장별 소스는 MarketDataPort
    어댑터가 감춘다(KR=네이버→KIS 폴백, US=네이버 foreign). 미국 심볼도 같은 PriceCandle 에 저장."""
    end = datetime.now()
    start = (
        datetime(since.year, since.month, since.day) - timedelta(days=1)
        if since
        else end - timedelta(days=RANGE_DAYS[tf])
    )
    fresh = _market_data(market).fetch_periodic(code, tf, start, end)
    return batch_upsert_periodic(db, code, Timeframe(tf), fresh)


def refresh_periodic(code: str, tf: str, market: str = "KR") -> None:
    """백그라운드 증분 갱신 — 자체 세션. 마지막 bar 이후만 조회·적재한다(국내/미국 공용).

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
            n = _fetch_and_store(db, code, tf, since=latest, market=market)
            logger.info("candle refresh %s (%s): +%d bars (since=%s)", key, market, n, latest)
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
    fresh = _market_data("KR").fetch_intraday_30min(code)
    if fresh:
        intraday.upsert_intraday(db, code, fresh)
    return read_intraday(db, code, days)


def refresh_intraday(code: str) -> None:
    """백그라운드 30분봉 갱신 — 자체 세션. 가용 분봉 리샘플·누적."""
    key = f"{code}|30m"
    if not _cooldown_ok(key, _INTRADAY_COOLDOWN_S):
        return
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)
    try:
        db = SessionLocal()
        try:
            fresh = _market_data("KR").fetch_intraday_30min(code)
            if fresh:
                intraday.upsert_intraday(db, code, fresh)
        finally:
            db.close()
    except Exception as e:
        logger.warning("intraday refresh failed %s: %s", key, e)
    finally:
        with _inflight_lock:
            _inflight.discard(key)
