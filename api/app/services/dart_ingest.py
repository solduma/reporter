"""DART 공시 적재 — corp_code 매핑 보장 + 공시 조회·센티먼트·저장."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import CorpCodeMap, Disclosure, Sentiment
from app.services import dart
from app.services import sentiment as sentiment_svc
from reporter.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


def ensure_corp_mappings(db: Session, settings: Settings, session: requests.Session) -> None:
    """corp_code_map 이 비어 있으면 corpCode.xml 을 적재한다."""
    if db.scalar(select(func.count()).select_from(CorpCodeMap)):
        return
    mappings = dart.fetch_corp_mappings(settings.dart_api_key, session)
    for m in mappings:
        stmt = insert(CorpCodeMap).values(
            stock_code=m.stock_code, corp_code=m.corp_code, corp_name=m.corp_name
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_code"],
            set_={"corp_code": stmt.excluded.corp_code, "corp_name": stmt.excluded.corp_name},
        )
        db.execute(stmt)
    db.commit()
    logger.info("loaded %d corp mappings", len(mappings))


# 이 시간 안에 이미 동기화한 종목은 DART 재조회를 건너뛴다(매 페이지 조회 시 지연 방지).
_SYNC_TTL = timedelta(hours=6)


def sync_disclosures(
    db: Session, settings: Settings, stock_code: str, begin: date, end: date
) -> int:
    """종목의 공시를 조회·센티먼트 분류·저장한다. 신규 저장 수를 반환한다.

    최근 _SYNC_TTL 안에 동기화한 이력이 있으면 DART 재조회를 건너뛰고 0 을 반환한다.
    """
    latest = db.scalar(
        select(func.max(Disclosure.created_at)).where(Disclosure.stock_code == stock_code)
    )
    if latest and datetime.now(UTC) - latest < _SYNC_TTL:
        return 0  # 최근 동기화됨 → DB 캐시만 사용

    session = requests.Session()
    ensure_corp_mappings(db, settings, session)

    corp_code = db.scalar(
        select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == stock_code)
    )
    if not corp_code:
        logger.info("no corp_code for %s", stock_code)
        return 0

    fetched = dart.fetch_disclosures(
        settings.dart_api_key, corp_code, stock_code, begin, end, session
    )
    if not fetched:
        return 0

    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    saved = 0
    for d in fetched:
        if db.scalar(select(Disclosure).where(Disclosure.rcept_no == d.rcept_no)):
            continue  # 멱등: 이미 저장됨
        sent = sentiment_svc.classify_disclosure(client, settings.insight_model, d.report_nm)
        db.add(
            Disclosure(
                stock_code=d.stock_code,
                corp_code=d.corp_code,
                rcept_no=d.rcept_no,
                report_nm=d.report_nm,
                flr_nm=d.flr_nm,
                rcept_dt=d.rcept_dt,
                dart_url=d.dart_url,
                sentiment=Sentiment(sent.sentiment),
                rationale=sent.rationale,
            )
        )
        db.commit()
        saved += 1
    logger.info("synced %d new disclosures for %s", saved, stock_code)
    return saved
