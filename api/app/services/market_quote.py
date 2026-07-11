"""지수·환율 시세 시계열 적재/조회 — 대시보드 소스를 DB로.

us_market.fetch_* 는 120s 인메모리 캐시만 있어 워커 재시작 시 소실되고 시계열이 남지 않는다.
이 서비스는 스냅샷을 market_quote 에 쌓아(최근값 조회 + 이력 보존), 대시보드가 DB 우선으로
읽게 한다. 스냅샷은 조회 시(신선하지 않으면) 또는 배치에서 적재한다.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import MarketQuote
from app.db.session import SessionLocal
from reporter import us_market

logger = logging.getLogger(__name__)

# 스냅샷 단일 실행 가드 — 시세 TTL(30s)이 짧아 폴링마다 is_stale 이 True 가 되면 매 요청이
# 백그라운드 스냅샷을 예약한다. 동시 뷰어 N 명이면 스냅샷 스레드가 우르르 떠 네이버를 몰아치므로,
# 이미 도는 스냅샷이 있으면 새 요청은 건너뛴다(single-flight).
_snapshot_inflight = False
_snapshot_lock = threading.Lock()


def snapshot_quotes_bg() -> None:
    """백그라운드 지수·환율 스냅샷 적재 — 자체 세션. 이미 실행 중이면 건너뛴다. 실패 흡수."""
    global _snapshot_inflight
    with _snapshot_lock:
        if _snapshot_inflight:
            return
        _snapshot_inflight = True
    db = SessionLocal()
    try:
        snapshot_quotes(db)
    except Exception as e:
        db.rollback()
        logger.warning("market quote snapshot failed: %s", e)
    finally:
        db.close()
        with _snapshot_lock:
            _snapshot_inflight = False

# 스냅샷 주기 — 의사 실시간(대시보드 수십초 갱신). ts 는 분 버킷이라 같은 분 재조회는 같은 행을
# 덮어써(행 폭증 없이) 값만 신선해진다. us_market 인메모리 캐시(_CACHE_TTL)와 함께 낮춘다.
_SNAPSHOT_TTL = timedelta(seconds=30)


def _fetchers():
    return (("us", us_market.fetch_us_indices), ("kr", us_market.fetch_kr_indices), ("fx", us_market.fetch_exchange_rates))


def snapshot_quotes(db: Session) -> int:
    """지수·환율을 조회해 market_quote 에 스냅샷 저장한다. 저장 건수 반환.

    같은 (name, ts) 는 upsert. ts 는 분 단위로 버킷팅해 과도한 행 증가를 막는다.
    """
    # 분 버킷 타임스탬프(초·마이크로초 절삭).
    now = datetime.now(UTC)
    bucket = now.replace(second=0, microsecond=0)
    saved = 0
    for kind, fetch in _fetchers():
        for q in fetch():
            stmt = insert(MarketQuote).values(
                name=q.name, kind=kind, ts=bucket,
                close=q.close, change=q.change, change_ratio=q.change_ratio, rising=q.rising,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_market_quote",
                set_={
                    "close": stmt.excluded.close, "change": stmt.excluded.change,
                    "change_ratio": stmt.excluded.change_ratio, "rising": stmt.excluded.rising,
                    "kind": stmt.excluded.kind,
                },
            )
            db.execute(stmt)
            saved += 1
    db.commit()
    return saved


_KINDS = ("us", "kr", "fx")


def is_stale(db: Session) -> bool:
    """어느 한 kind 라도 스냅샷이 없거나 TTL 보다 오래됐으면 True.

    전역 max(ts) 로 판단하면 한 kind(예: kr) 조회가 실패해도 다른 kind 가 방금 갱신돼
    신선한 것으로 오판한다. kind 별 최신 시각을 각각 검사해 부분 실패를 가린다.
    """
    latest_by_kind = dict(
        db.execute(
            select(MarketQuote.kind, func.max(MarketQuote.ts)).group_by(MarketQuote.kind)
        ).all()
    )
    now = datetime.now(UTC)
    for kind in _KINDS:
        ts = latest_by_kind.get(kind)
        if ts is None or now - ts >= _SNAPSHOT_TTL:
            return True
    return False


def latest_quotes(db: Session, kind: str) -> list[MarketQuote]:
    """해당 kind(us|kr|fx)의 시세들 — 각 name 의 최신 버킷값. 없으면 빈 리스트.

    전역 최신 시각 한 점만 쓰면 그 스냅샷에서 빠진(조회 실패한) name 은 통째로 사라진다.
    name 별 최신 ts 를 잡아 마지막 성공값이라도 노출한다.
    """
    latest_ts = (
        select(MarketQuote.name, func.max(MarketQuote.ts).label("ts"))
        .where(MarketQuote.kind == kind)
        .group_by(MarketQuote.name)
        .subquery()
    )
    return list(
        db.scalars(
            select(MarketQuote)
            .join(
                latest_ts,
                (MarketQuote.name == latest_ts.c.name) & (MarketQuote.ts == latest_ts.c.ts),
            )
            .where(MarketQuote.kind == kind)
            .order_by(MarketQuote.id)  # 최초 적재 순서(=조회 순서) 유지로 표시 순서 안정화
        ).all()
    )
