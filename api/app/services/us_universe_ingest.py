"""US 유니버스 스냅샷 적재 — 시드 종목의 네이버 시세를 오늘 날짜로 upsert(스크리너 소스).

S&P500(+보충) ~600종목을 종목당 네이버 1콜로 시총·PER/PBR·거래대금·52주를 받아 us_universe 에
쌓는다. 종목 간 간격을 둬 네이버 연타 차단을 피한다(KR growth_ingest 패턴). 야간 배치.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import us_universe as source
from app.adapters.market import naver
from app.db.models import PriceCandle, SyncState, Timeframe, UsUniverse
from app.services import candle_service, sync_state

logger = logging.getLogger(__name__)

_STOCK_INTERVAL_S = 0.15  # 종목 간 간격(네이버 무인증 연타 차단 회피)


def _momentum_3m(db: Session, naver_symbol: str) -> float | None:
    """저장된 US 일봉(candle_service 가 조회 시 적재)에서 3개월(약 63거래일) 수익률%. 없으면 None."""
    rows = list(
        db.scalars(
            select(PriceCandle.close)
            .where(PriceCandle.stock_code == naver_symbol, PriceCandle.timeframe == Timeframe.DAY)
            .order_by(PriceCandle.bar_date.desc())
            .limit(64)
        ).all()
    )
    if len(rows) < 64 or not rows[-1]:
        return None
    last, past = rows[0], rows[-1]
    return round((last / past - 1) * 100, 1) if past else None


def latest_snapshot_date(db: Session) -> date | None:
    return db.scalar(select(UsUniverse.snapshot_date).order_by(UsUniverse.snapshot_date.desc()).limit(1))


def snapshot_us_universe(db: Session, snapshot_date: date | None = None) -> dict:
    """시드 종목을 오늘 날짜 us_universe 스냅샷으로 적재. {seeded, saved, skipped} 반환."""
    snapshot_date = snapshot_date or datetime.now(UTC).date()
    session = requests.Session()
    seeds = source.seed_tickers(session)
    saved = skipped = 0
    for ticker, sector in seeds:
        time.sleep(_STOCK_INTERVAL_S)
        row = source.fetch_row(ticker, sector, session)
        if row is None or row.market_cap is None:
            skipped += 1
            continue
        values = {
            "naver_symbol": row.naver_symbol,
            "name": row.name,
            "exchange": row.exchange,
            "sector": row.sector,
            "close_price": row.close_price,
            "change_pct": row.change_pct,
            "market_cap": row.market_cap,
            "trading_value": row.trading_value,
            "per": row.per,
            "pbr": row.pbr,
            "eps": row.eps,
            "high_52w": row.high_52w,
            "low_52w": row.low_52w,
            "momentum_3m": _momentum_3m(db, row.naver_symbol),
        }
        stmt = insert(UsUniverse).values(snapshot_date=snapshot_date, ticker=ticker, **values)
        stmt = stmt.on_conflict_do_update(constraint="uq_us_universe", set_=values)
        db.execute(stmt)
        saved += 1
    db.commit()
    logger.info("us universe snapshot %s: %d seeded, %d saved, %d skipped", snapshot_date, len(seeds), saved, skipped)
    return {"seeded": len(seeds), "saved": saved, "skipped": skipped}


# ── US 일봉 10년 점진 백필 (야간, 재개 가능) ───────────────────────────────
# US 봉은 온디맨드(차트 조회 시)에만 적재돼 대부분 종목이 봉 0 → momentum_3m None(스크리너 누락).
# KR candle_ingest.run_backfill_progressive 와 동일 패턴으로 유니버스 전 심볼을 네이버 foreign 봉으로
# 10년 백필한다. 조회만 스레드풀 병렬, DB 쓰기·마킹은 호출 스레드 단일 세션(Session 스레드 비안전).
_US_BACKFILL_DOMAIN = "us_candle_10y"  # SyncState 도메인(완료 심볼 마킹 → 재개)
_US_BACKFILL_PER_RUN = 600  # US 유니버스 ~600. 하룻밤 전량(크래시 시 마킹으로 재개)
_US_BACKFILL_WORKERS = 6  # 네이버 무인증 동시 조회(KR 백필과 동일 보수치)
_US_DAY_RANGE_DAYS = 365 * 10 + 30  # 10년


def _us_universe_symbols(db: Session) -> list[str]:
    """최신 스냅샷의 US 유니버스 네이버 심볼(봉 저장 키). 없으면 빈 리스트."""
    snap = latest_snapshot_date(db)
    if snap is None:
        return []
    return list(
        db.scalars(
            select(UsUniverse.naver_symbol).where(UsUniverse.snapshot_date == snap)
        ).all()
    )


def _us_backfilled_symbols(db: Session) -> set[str]:
    """이미 10년 백필 완료로 마킹된 US 심볼(재개 시 재처리 방지)."""
    return set(
        db.scalars(select(SyncState.stock_code).where(SyncState.domain == _US_BACKFILL_DOMAIN)).all()
    )


def _recompute_us_momentum(db: Session, symbols: list[str]) -> int:
    """백필된 심볼의 momentum_3m 을 저장 봉으로 재계산해 최신 스냅샷 행에 반영. 갱신 건수."""
    snap = latest_snapshot_date(db)
    if snap is None:
        return 0
    updated = 0
    for sym in symbols:
        mom = _momentum_3m(db, sym)
        if mom is None:
            continue
        db.execute(
            update(UsUniverse)
            .where(UsUniverse.snapshot_date == snap, UsUniverse.naver_symbol == sym)
            .values(momentum_3m=mom)
        )
        updated += 1
    db.commit()
    return updated


def run_candle_backfill_progressive(
    db: Session, per_run: int = _US_BACKFILL_PER_RUN, workers: int = _US_BACKFILL_WORKERS
) -> dict:
    """US 유니버스 심볼의 일봉을 10년치로 병렬 백필(재개 가능) 후 momentum_3m 재계산.

    완료 심볼은 SyncState(us_candle_10y)로 마킹해 중단 시 다음 실행이 이어받는다.
    반환: {done, failed, remaining, momentum_updated}.
    """
    symbols = _us_universe_symbols(db)
    if not symbols:
        logger.warning("no US universe symbols; skip US candle backfill")
        return {"done": 0, "failed": 0, "remaining": 0, "momentum_updated": 0}

    already = _us_backfilled_symbols(db)
    pending = [s for s in symbols if s not in already]
    batch = pending[:per_run]
    end = datetime.now()
    start = end - timedelta(days=_US_DAY_RANGE_DAYS)

    def _fetch(sym: str) -> tuple[str, list]:
        with requests.Session() as session:  # 스레드 간 세션 공유 금지
            return sym, naver.fetch_periodic_foreign(sym, "day", start, end, session)

    def _store(sym: str, candles: list) -> None:
        if candles:
            candle_service.batch_upsert_periodic(db, sym, Timeframe.DAY, candles)  # 자체 commit
        sync_state.mark(db, _US_BACKFILL_DOMAIN, sym)
        db.commit()  # 멱등 upsert — 마킹 누락돼도 다음 실행 재적재 무해

    done = failed = 0
    window = workers * 4  # 동시 상주 결과 제한(메모리, KR 백필과 동일)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(batch), window):
            futures = {pool.submit(_fetch, s): s for s in batch[i : i + window]}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, candles = fut.result()
                    if not candles:  # 빈 응답: 스로틀/진짜없음 구분 위해 1회 순차 재조회
                        _, candles = _fetch(sym)
                        if not candles:
                            logger.info("US 10y backfill empty (marking done): %s", sym)
                    _store(sym, candles)
                    done += 1
                except Exception as e:  # 한 심볼 실패가 배치를 막지 않도록(다음 실행 재시도)
                    db.rollback()
                    failed += 1
                    logger.warning("US 10y backfill failed for %s: %s", sym, e)
            futures.clear()

    momentum_updated = _recompute_us_momentum(db, batch)
    remaining = len(pending) - done
    logger.info(
        "US 10y candle backfill: done=%d failed=%d remaining=%d momentum_updated=%d",
        done, failed, remaining, momentum_updated,
    )
    return {"done": done, "failed": failed, "remaining": remaining, "momentum_updated": momentum_updated}
