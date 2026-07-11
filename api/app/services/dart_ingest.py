"""DART 공시 적재 — corp_code 매핑 보장 + 공시 조회·센티먼트·저장."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.config import Settings
from app.db.models import CorpCodeMap, Disclosure, DisclosureSyncState, Sentiment
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
    공시가 0건인 종목도 재조회를 억제하도록 마지막 동기화 시각을 별도로 기록한다.
    """
    last_synced = db.scalar(
        select(DisclosureSyncState.synced_at).where(
            DisclosureSyncState.stock_code == stock_code
        )
    )
    if last_synced and datetime.now(UTC) - last_synced < _SYNC_TTL:
        return 0  # 최근 동기화됨 → DB 캐시만 사용

    session = requests.Session()
    ensure_corp_mappings(db, settings, session)

    corp_code = db.scalar(
        select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == stock_code)
    )
    if not corp_code:
        logger.info("no corp_code for %s", stock_code)
        _mark_synced(db, stock_code)  # 비상장 등도 TTL 동안 재조회 억제
        return 0

    fetched = dart.fetch_disclosures(
        settings.dart_api_key, corp_code, stock_code, begin, end, session
    )

    # 이미 저장된 rcept_no 를 한 번에 조회해 GLM 분류 대상만 추린다.
    fetched_nos = [d.rcept_no for d in fetched]
    existing = set(
        db.scalars(
            select(Disclosure.rcept_no).where(Disclosure.rcept_no.in_(fetched_nos))
        ).all()
    )

    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    saved = 0
    for d in fetched:
        if d.rcept_no in existing:
            continue  # 멱등: 이미 저장됨
        # 원문 발췌(앞 6000자)까지 읽어 판단. 조회 실패 시 제목-only 로 폴백.
        body = dart.fetch_document_text(settings.dart_api_key, d.rcept_no, session)
        sent = sentiment_svc.classify_disclosure(
            client, settings.insight_model, d.report_nm, body
        )
        # 동시 요청 경쟁에도 안전하도록 on_conflict_do_nothing 로 삽입한다.
        stmt = (
            insert(Disclosure)
            .values(
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
            .on_conflict_do_nothing(constraint="uq_disclosure_rcept")
        )
        db.execute(stmt)
        db.commit()
        saved += 1

    _mark_synced(db, stock_code)  # 신규 0건이어도 기록해 재조회를 억제
    logger.info("synced %d new disclosures for %s", saved, stock_code)
    return saved


def _mark_synced(db: Session, stock_code: str) -> None:
    stmt = insert(DisclosureSyncState).values(stock_code=stock_code, synced_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_code"], set_={"synced_at": func.now()}
    )
    db.execute(stmt)
    db.commit()
