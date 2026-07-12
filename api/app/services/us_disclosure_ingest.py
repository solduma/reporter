"""US 공시(SEC 8-K) 수집·조회 — us_universe 종목의 최근 8-K 를 us_disclosures 로.

수집 시엔 LLM 을 쓰지 않고 item 코드→한글 요약(UsDisclosurePort.describe_8k_items)만 title 에 넣어 비용 0.
sentiment/rationale 는 상세 타임라인 조회 시 필요하면 LLM 으로 채운다(4.4, 비용 통제).
야간 배치 — SEC submissions 종목당 1콜(throttle 0.12s).
"""

from __future__ import annotations

import logging
from datetime import date

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.sec.disclosure_adapter import SecDisclosureAdapter
from app.config import Settings, get_settings
from app.db.models import UsDisclosure, UsUniverse
from app.ports.disclosure import UsDisclosurePort
from app.services import us_universe_ingest

logger = logging.getLogger(__name__)

_PER_FILING = 8  # 종목당 최근 8-K 수집 상한


# 포트 공급자 seam — 기본은 SecDisclosureAdapter, 테스트가 훅 교체로 fake 주입 가능.
def _disclosures(settings: Settings) -> UsDisclosurePort:
    return SecDisclosureAdapter(settings)


def _universe_tickers(db: Session) -> list[str]:
    as_of = us_universe_ingest.latest_snapshot_date(db)
    if not as_of:
        return []
    return list(db.scalars(select(UsUniverse.ticker).where(UsUniverse.snapshot_date == as_of)).all())


def sync_8k(db: Session, ticker: str, settings: Settings, session: requests.Session) -> int:
    """한 종목의 최근 8-K 를 us_disclosures 로 upsert. 저장 건수. CIK 미해석이면 0."""
    disc = _disclosures(settings)
    cik = disc.resolve_cik(ticker, session)
    if cik is None:
        return 0
    filings = disc.fetch_recent_filings(cik, forms=("8-K",), limit=_PER_FILING, session=session)
    saved = 0
    for f in filings:
        values = {
            "ticker": ticker,
            "cik": str(cik),
            "form_type": f.form,
            "filing_date": date.fromisoformat(f.filing_date),
            "primary_doc_url": f.primary_doc_url,
            "title": disc.describe_8k_items(f.items),
        }
        stmt = (
            insert(UsDisclosure)
            .values(accession=f.accession, **values)
            .on_conflict_do_nothing(constraint="uq_us_disclosure")
        )
        result = db.execute(stmt)
        saved += result.rowcount or 0  # 실제 삽입된 행만(재수집 시 중복은 0)
    db.commit()
    return saved


def run_us_disclosure_batch(db: Session, settings: Settings | None = None) -> dict:
    """유니버스 전 종목의 최근 8-K 를 수집한다(야간 배치). {tickers, filings} 반환."""
    settings = settings or get_settings()
    tickers = _universe_tickers(db)
    session = requests.Session()
    total = 0
    for t in tickers:
        try:
            total += sync_8k(db, t, settings, session)
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            logger.warning("us 8-K sync failed %s: %s", t, e)
    logger.info("us 8-K batch: %d tickers, %d filings", len(tickers), total)
    return {"tickers": len(tickers), "filings": total}


def recent_disclosures(db: Session, ticker: str, limit: int = 20) -> list[UsDisclosure]:
    """종목의 최근 공시(filing_date 내림차순). 상세 타임라인용."""
    return list(
        db.scalars(
            select(UsDisclosure)
            .where(UsDisclosure.ticker == ticker.upper())
            .order_by(UsDisclosure.filing_date.desc())
            .limit(limit)
        ).all()
    )
