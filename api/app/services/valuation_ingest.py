"""EV/EBITDA·PSR 산출 적재 — DART 재무제표(EBITDA·순차입금) + 시총 + 매출TTM.

main.naver 스크래핑(quote)에는 EBITDA·순차입금이 없어 EV/EBITDA 를 못 만든다. 이 서비스는
DART 전체재무제표(fnlttSinglAcntAll)에서 영업이익·감가상각·차입금·현금을 받아 EBITDA·순차입금을
계산하고, 시가총액(UniverseSnapshot)과 합쳐 EV/EBITDA·PSR 을 Financial 행에 채운다.

- EV = 시가총액 + 순차입금(총차입 - 현금)
- EV/EBITDA = EV / EBITDA(영업이익 + 감가상각비)
- PSR = 시가총액 / 매출TTM(최근 4개 분기 매출 합)
전부 원 단위. 무거워(분기당 DART 콜) 조회 시 캐시-aside + 저녁 배치로 채운다.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import CorpCodeMap, Financial, UniverseSnapshot, ValuationSyncState
from app.services import dart

logger = logging.getLogger(__name__)

_SYNC_TTL = timedelta(hours=24)  # 재무는 분기 단위라 하루 1회면 충분
_MONTH_TO_Q = {3: 1, 6: 2, 9: 3, 12: 4}


def _period_to_year_q(period: str) -> tuple[int, int] | None:
    """'2026.03' → (2026, 1). 분기말(3/6/9/12)만 매핑. 추정치·비분기면 None."""
    m = re.match(r"(\d{4})\.(\d{2})", period)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    q = _MONTH_TO_Q.get(month)
    return (year, q) if q else None


def _latest_market_cap(db: Session, code: str) -> float | None:
    """최신 스냅샷 시가총액(원). PSR·EV 공용."""
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    if not as_of:
        return None
    mc = db.scalar(
        select(UniverseSnapshot.market_cap).where(
            UniverseSnapshot.snapshot_date == as_of, UniverseSnapshot.stock_code == code
        )
    )
    return float(mc) if mc else None


def _ttm_revenue(rows: list[Financial], upto_period: str) -> float | None:
    """upto_period 를 포함한 최근 4개 분기 매출 합(TTM). 4개 미만이면 None."""
    quarters = [r for r in rows if not r.is_estimate and _period_to_year_q(r.period)]
    quarters.sort(key=lambda r: r.period)
    idx = next((i for i, r in enumerate(quarters) if r.period == upto_period), None)
    if idx is None or idx < 3:
        return None
    window = quarters[idx - 3 : idx + 1]
    revs = [r.revenue for r in window]
    if any(v is None for v in revs):
        return None
    return sum(revs)  # 억원 단위(quote 저장 단위)


def _mark_synced(db: Session, code: str) -> None:
    from sqlalchemy.dialects.postgresql import insert

    stmt = insert(ValuationSyncState).values(stock_code=code, synced_at=func.now())
    stmt = stmt.on_conflict_do_update(index_elements=["stock_code"], set_={"synced_at": func.now()})
    db.execute(stmt)
    db.commit()


def sync_valuation(db: Session, settings: Settings, code: str) -> int:
    """종목의 분기별 EV/EBITDA·PSR 을 산출해 Financial 에 채운다. 갱신 행 수 반환.

    24h TTL 내 재동기화는 건너뛴다. DART 키 없으면 0.
    """
    if not settings.dart_api_key:
        return 0
    last = db.scalar(
        select(ValuationSyncState.synced_at).where(ValuationSyncState.stock_code == code)
    )
    if last and datetime.now(UTC) - last < _SYNC_TTL:
        return 0

    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        _mark_synced(db, code)
        return 0

    rows = list(
        db.scalars(select(Financial).where(Financial.stock_code == code)).all()
    )
    market_cap = _latest_market_cap(db, code)
    session = requests.Session()

    # 1차: 분기별 EBITDA(원)·순차입금 원자료를 DART 에서 받아 채운다.
    q_rows = [r for r in rows if not r.is_estimate and _period_to_year_q(r.period)]
    q_rows.sort(key=lambda r: r.period)
    for r in q_rows:
        year, quarter = _period_to_year_q(r.period)
        st = dart.fetch_financial_statement(settings.dart_api_key, corp_code, year, quarter, session)
        if st is None:
            continue
        r.ebitda = st.ebitda
        r.net_debt = st.net_debt

    # 2차: EV/EBITDA 는 **TTM EBITDA**(최근 4개 분기 합)로, PSR 은 TTM 매출로 계산한다
    # (단일 분기 EBITDA 는 배수가 4배 왜곡되므로). EV(원)=시총(원)+순차입(원).
    updated = 0
    for i, r in enumerate(q_rows):
        ttm_ebitda = _ttm_sum([q.ebitda for q in q_rows[max(0, i - 3) : i + 1]]) if i >= 3 else None
        ev = (market_cap + r.net_debt) if (market_cap is not None and r.net_debt is not None) else None
        r.ev_ebitda = round(ev / ttm_ebitda, 2) if (ev is not None and ttm_ebitda) else None
        ttm_rev_eok = _ttm_revenue(rows, r.period)  # 억원
        ttm_rev = ttm_rev_eok * 1e8 if ttm_rev_eok is not None else None  # 원
        r.psr = round(market_cap / ttm_rev, 2) if (market_cap and ttm_rev) else None
        updated += 1
    db.commit()
    _mark_synced(db, code)
    logger.info("valuation synced for %s: %d periods", code, updated)
    return updated


def _ttm_sum(values: list[float | None]) -> float | None:
    """4개 분기 값 합(TTM). 하나라도 None 이거나 4개 미만이면 None."""
    if len(values) < 4 or any(v is None for v in values):
        return None
    return sum(values)
