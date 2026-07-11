"""US 종목 재무 조회·동기화 서비스 — SEC EDGAR 재무 + 네이버 시세로 US 밸류에이션 산출.

DB 우선(us_financials 스냅샷) + TTL 만료 시 재계산. 시총은 EDGAR 에 없어 (네이버 종가 x
EDGAR 주식수)로 근사한다. 계산은 domain.us_financials, 외부 IO 는 sec/us_market 어댑터.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import sec
from app.config import get_settings
from app.db.models import UsFinancial
from app.domain import us_financials
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
