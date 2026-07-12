"""DART 공시 적재 — corp_code 매핑 보장 + 공시 조회·센티먼트·저장."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.adapters.dart.client import extract_ownership_reason
from app.adapters.dart.disclosure_adapter import DartDisclosureAdapter
from app.adapters.llm import get_llm
from app.config import Settings
from app.db.models import CorpCodeMap, Disclosure, DisclosureSyncState, Sentiment
from app.domain.disclosure import summarize_ownership
from app.ports.disclosure import KrDisclosurePort
from app.services import sentiment as sentiment_svc

logger = logging.getLogger(__name__)

# 이 키워드가 report_nm 에 있으면 임원·주요주주 소유변동(elestock 구조화) 요약을 보강한다.
_OWNERSHIP_REPORT_KW = "소유상황보고서"


# 포트 공급자 seam — 기본은 DartDisclosureAdapter, 테스트가 훅 교체로 fake 주입 가능.
def _disclosures(settings: Settings) -> KrDisclosurePort:
    return DartDisclosureAdapter(settings.dart_api_key)


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

    disc = _disclosures(settings)
    fetched = disc.fetch_disclosures(corp_code, stock_code, begin, end, session)

    # 이미 저장된 rcept_no 를 한 번에 조회해 GLM 분류 대상만 추린다.
    fetched_nos = [d.rcept_no for d in fetched]
    existing = set(
        db.scalars(
            select(Disclosure.rcept_no).where(Disclosure.rcept_no.in_(fetched_nos))
        ).all()
    )

    client = get_llm(settings)
    if client is None:
        # LLM 없으면 센티먼트 분류 불가 → 공시를 HOLD 로 영구 오적재하지 않도록 저장을 건너뛴다
        # (다음에 키가 생기면 재분류되도록 _mark_synced 도 하지 않는다).
        logger.warning("no LLM (OLLAMA_API_KEY); skip disclosure sentiment for %s", stock_code)
        return 0

    # 소유상황보고서가 신규로 하나라도 있으면 elestock 을 corp 단위로 한 번만 조회해 방향을 확보한다.
    ownership_changes: dict = {}
    if any(_OWNERSHIP_REPORT_KW in d.report_nm and d.rcept_no not in existing for d in fetched):
        ownership_changes = disc.fetch_ownership_changes(corp_code, session)

    saved = 0
    for d in fetched:
        if d.rcept_no in existing:
            continue  # 멱등: 이미 저장됨
        # 원문 발췌(앞 6000자)까지 읽어 판단. 조회 실패 시 제목-only 로 폴백.
        body = disc.fetch_document_text(d.rcept_no, session)
        # 소유상황보고서면 구조화 증감(방향·수량)+문서에서 뽑은 사유를 요약해 판단 근거로 넣는다.
        ownership_summary = ""
        change = ownership_changes.get(d.rcept_no)
        if change is not None:
            change.reason = extract_ownership_reason(body)
            ownership_summary = summarize_ownership(change)
        sent = sentiment_svc.classify_disclosure(
            client, settings.insight_model, d.report_nm, body, ownership_summary
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
