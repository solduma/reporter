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
    """upto_period 를 포함한 **연속 4개 분기** 매출 합(TTM). 결측·불연속이면 None.

    네이버 재무는 분기별 개별 매출(누적 아님)이라 단순 합이 TTM 이다. 단 분기가 빠지면
    (예: 6월 누락) 15개월치를 TTM 으로 오인하지 않도록 (year, quarter) 연속성을 확인한다.
    """
    by_yq: dict[tuple[int, int], float | None] = {}
    for r in rows:
        if r.is_estimate:
            continue
        yq = _period_to_year_q(r.period)
        if yq:
            by_yq[yq] = r.revenue
    target = _period_to_year_q(upto_period)
    if not target:
        return None
    total = 0.0
    cursor = target
    for _ in range(4):
        v = by_yq.get(cursor)
        if v is None:
            return None
        total += v
        cursor = _prev_yq(cursor)
    return total  # 억원 단위(quote 저장 단위)


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

    # 1차: 분기별 누적(YTD) EBITDA(원)·순차입금 원자료를 DART 에서 받는다.
    # 한국 DART 는 손익을 **회계연도 누적**으로 보고한다: Q1=3개월·반기=6개월·3Q=9개월·
    # 사업보고서(Q4)=12개월. 따라서 여기 st.ebitda 는 '해당 분기까지의 누적' 값이다.
    q_rows = [r for r in rows if not r.is_estimate and _period_to_year_q(r.period)]
    q_rows.sort(key=lambda r: _period_to_year_q(r.period))
    ytd_ebitda: dict[tuple[int, int], float | None] = {}
    for r in q_rows:
        year, quarter = _period_to_year_q(r.period)
        st = dart.fetch_financial_statement(settings.dart_api_key, corp_code, year, quarter, session)
        if st is None:
            continue
        r.ebitda = st.ebitda  # 누적 EBITDA 원자료(표시·디버깅용 보존)
        r.net_debt = st.net_debt
        ytd_ebitda[(year, quarter)] = st.ebitda

    # 2차: 누적 EBITDA 를 분기별 개별값으로 환산(Qn = YTDn - YTD(n-1), Q1은 그대로) 후,
    # **연속한 4개 분기** 합으로 TTM EBITDA 를 만든다. PSR 은 TTM 매출(억원→원).
    # EV(원)=시총(원)+순차입(원). 단위는 셋 다 원이라 나눗셈에서 상쇄된다.
    updated = 0
    for r in q_rows:
        yq = _period_to_year_q(r.period)
        # 연간(.12) 행의 ev_ebitda 는 report_ingest(원문 XML 정밀 D&A)가 소유 → 여기선 안 건드림
        # (방법론 혼재로 값이 튀는 것 방지). 분기(.03/.06/.09)만 여기서 채운다.
        if not r.period.endswith(".12"):
            ttm_ebitda = _ttm_ebitda(ytd_ebitda, yq)
            ev = (market_cap + r.net_debt) if (market_cap is not None and r.net_debt is not None) else None
            r.ev_ebitda = round(ev / ttm_ebitda, 2) if (ev is not None and ttm_ebitda and ttm_ebitda > 0) else None
        ttm_rev_eok = _ttm_revenue(rows, r.period)  # 억원
        ttm_rev = ttm_rev_eok * 1e8 if ttm_rev_eok is not None else None  # 원
        r.psr = round(market_cap / ttm_rev, 2) if (market_cap and ttm_rev and ttm_rev > 0) else None
        updated += 1
    db.commit()
    _mark_synced(db, code)
    logger.info("valuation synced for %s: %d periods", code, updated)
    return updated


def _prev_yq(yq: tuple[int, int]) -> tuple[int, int]:
    """직전 분기. Q1 이전은 전년 Q4."""
    year, q = yq
    return (year - 1, 4) if q == 1 else (year, q - 1)


def _discrete_ebitda(ytd: dict[tuple[int, int], float | None], yq: tuple[int, int]) -> float | None:
    """누적(YTD) EBITDA 를 해당 분기 단일 값으로 환산. Q1=YTD 그대로, 그 외=YTDn-YTD(n-1)."""
    cur = ytd.get(yq)
    if cur is None:
        return None
    if yq[1] == 1:
        return cur
    prev = ytd.get(_prev_yq(yq))
    return None if prev is None else cur - prev


def _ttm_ebitda(ytd: dict[tuple[int, int], float | None], yq: tuple[int, int]) -> float | None:
    """yq 를 포함한 **연속 4개 분기** 개별 EBITDA 합(TTM). 하나라도 결측·불연속이면 None."""
    total = 0.0
    cursor = yq
    for _ in range(4):
        v = _discrete_ebitda(ytd, cursor)
        if v is None:
            return None
        total += v
        cursor = _prev_yq(cursor)
    return total
