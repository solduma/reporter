"""US 종목 재무 조회·동기화 서비스 — SEC EDGAR 재무 + 네이버 시세로 US 밸류에이션 산출.

DB 우선(us_financials 스냅샷) + TTL 만료 시 재계산. 시총은 EDGAR 에 없어 (네이버 종가 x
EDGAR 주식수)로 근사한다. 계산은 domain.us_financials, 외부 IO 는 sec/us_market 어댑터.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import sec
from app.config import Settings, get_settings
from app.db.models import SyncState, UsFinancial, UsUniverse
from app.domain import us_financials
from app.services import sync_state
from reporter import us_market

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=24)  # 재무는 분기 단위 → 하루 1회 재계산


@dataclass
class UsQuote:
    ticker: str
    naver_symbol: str  # 차트 조회용(/api/chart market=US)
    name: str | None
    close: float | None
    change_ratio: str | None
    rising: bool | None


def _parse_close(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def quote(ticker: str) -> UsQuote | None:
    """US 티커 현재 시세 + 네이버 차트 심볼. 없으면 None."""
    resolved = us_market.resolve_us_symbol(ticker.upper())
    if resolved is None:
        return None
    symbol, q = resolved
    settings = get_settings()
    return UsQuote(
        ticker=ticker.upper(),
        naver_symbol=symbol,
        name=sec.company_name(settings, ticker) or ticker.upper(),
        close=_parse_close(q.close),
        change_ratio=q.change_ratio,
        rising=q.rising,
    )


def _is_fresh(row: UsFinancial | None) -> bool:
    if row is None:
        return False
    ts = row.updated_at
    # Postgres 는 TIMESTAMPTZ(aware)지만 일부 백엔드(SQLite 테스트)는 naive 로 돌려줘 tz 를 보정한다.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return datetime.now(UTC) - ts < _TTL


def get_financials(db: Session, ticker: str, *, force: bool = False) -> UsFinancial | None:
    """US 종목 재무 스냅샷 — DB 우선, TTL 만료·force 시 SEC 에서 재계산·upsert. 없으면 None.

    시총 근사 = 네이버 현재 종가 x EDGAR 최신 주식수. CIK 미매핑(비상장 등)이면 None.
    """
    ticker = ticker.upper()
    row = db.get(UsFinancial, ticker)
    if not force and _is_fresh(row):
        return row

    settings = get_settings()
    cik = sec.resolve_cik(settings, ticker)
    if cik is None:
        return row  # SEC 미등록 — 기존값(있으면) 유지
    facts = sec.fetch_company_facts(settings, cik)
    if facts is None:
        return row

    resolved = us_market.resolve_us_symbol(ticker)
    close = _parse_close(resolved[1].close) if resolved else None
    shares = us_financials._latest_shares(facts)
    market_cap = (close * shares) if (close and shares) else None

    m = us_financials.compute(facts, market_cap)
    values = {
        "name": sec.company_name(settings, ticker) or ticker,
        "ttm_revenue": m.ttm_revenue,
        "ttm_net_income": m.ttm_net_income,
        "ttm_operating_income": m.ttm_operating_income,
        "ttm_eps": m.ttm_eps,
        "equity": m.equity,
        "shares": m.shares,
        "market_cap": market_cap,
        "per": m.per,
        "pbr": m.pbr,
        "psr": m.psr,
        "roe": m.roe,
        "updated_at": datetime.now(UTC),
    }
    stmt = (
        insert(UsFinancial)
        .values(ticker=ticker, **values)
        .on_conflict_do_update(index_elements=["ticker"], set_=values)
    )
    db.execute(stmt)
    db.commit()
    logger.info("us financials synced for %s (cik=%s)", ticker, cik)
    return db.get(UsFinancial, ticker)


# ── US 재무 점진 백필 (야간, 재개 가능) ──────────────────────────────────
# get_financials 는 종목당 SEC 콜(ticker→CIK 매핑 + companyfacts + company_name)이 무거워
# 유니버스 전체를 한 번에 못 돈다. KR financials_backfill 과 동일하게 per_run 씩 점진 백필하고
# sync_state(us_financials_10y)로 완료 마킹해 재개한다. SEC throttle(0.12s)이 rate limit 방어.
_BACKFILL_DOMAIN = "us_financials_10y"
_PER_RUN = 200  # SEC 종목당 ~3콜, throttle 0.12s → per_run=200 이면 ~1~2분


def _universe_tickers(db: Session) -> list[str]:
    """최신 US 유니버스 스냅샷의 ticker 목록. 없으면 빈 리스트."""
    snap = db.scalar(select(UsUniverse.snapshot_date).order_by(UsUniverse.snapshot_date.desc()).limit(1))
    if snap is None:
        return []
    return list(db.scalars(select(UsUniverse.ticker).where(UsUniverse.snapshot_date == snap)).all())


def _done_tickers(db: Session) -> set[str]:
    return set(
        db.scalars(select(SyncState.stock_code).where(SyncState.domain == _BACKFILL_DOMAIN)).all()
    )


def _reconcile_markers(db: Session, tickers: list[str], done: set[str]) -> int:
    """재무 행(per 등)이 이미 있는데 마커가 없는 종목의 완료 마커를 복원(SEC 재조회 없이).

    마커가 외부에서 삭제돼도 이미 채운 종목을 매일 재조회하지 않게 한다(KR reconcile 과 동형)."""
    missing = [t for t in tickers if t not in done]
    if not missing:
        return 0
    has_fin = set(
        db.scalars(
            select(UsFinancial.ticker)
            .where(UsFinancial.ticker.in_(missing), UsFinancial.per.isnot(None))
        ).all()
    )
    for t in has_fin:
        sync_state.mark(db, _BACKFILL_DOMAIN, t)
    if has_fin:
        db.commit()
        done.update(has_fin)
        logger.info("us financials backfill: 마커 %d개 복원(재무 보유·마커 결손)", len(has_fin))
    return len(has_fin)


def run_financials_backfill(db: Session, settings: Settings | None = None, per_run: int = _PER_RUN) -> dict:
    """US 유니버스 종목의 SEC 재무를 점진 백필한다(하룻밤 per_run 개, 재개 가능).

    반환: {done, failed, reconciled, remaining}. 종목당 SEC 콜이 많아 순차 처리(throttle 방어)."""
    tickers = _universe_tickers(db)
    if not tickers:
        logger.warning("no US universe tickers; skip us financials backfill")
        return {"done": 0, "failed": 0, "reconciled": 0, "remaining": 0}

    done_set = _done_tickers(db)
    reconciled = _reconcile_markers(db, tickers, done_set)  # 마커 결손분 복원(재조회 낭비 방지)
    pending = [t for t in tickers if t not in done_set]
    batch = pending[:per_run]
    done = failed = 0
    for ticker in batch:
        try:
            row = get_financials(db, ticker, force=True)
            # per 산출 성공분만 완료 마킹. CIK 미등록·facts 없음(row 그대로/None)은 재시도 여지 남김.
            if row is not None and row.per is not None:
                sync_state.mark(db, _BACKFILL_DOMAIN, ticker)
                db.commit()
                done += 1
            else:
                failed += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("us financials backfill failed for %s: %s", ticker, e)

    remaining = len(pending) - done
    logger.info(
        "us financials backfill: done=%d failed=%d reconciled=%d remaining=%d",
        done, failed, reconciled, remaining,
    )
    return {"done": done, "failed": failed, "reconciled": reconciled, "remaining": remaining}
