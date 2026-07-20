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

    최근 _SYNC_TTL 안에 동기화했고 그 동기화가 요청 창(begin)만큼 과거를 이미 커버했으면 DART
    재조회를 건너뛰고 0 을 반환한다. 요청이 더 과거(begin < synced_from)를 원하면 TTL 이 유효해도
    재조회한다(얕은 배치가 최근 stamp 한 뒤 2년 조회가 스킵되는 것 방지). 공시가 0건인 종목도
    재조회 억제를 위해 마지막 동기화 시각·깊이를 기록한다.
    """
    state = db.execute(
        select(DisclosureSyncState.synced_at, DisclosureSyncState.synced_from).where(
            DisclosureSyncState.stock_code == stock_code
        )
    ).first()
    # TTL 유효 + 요청 창이 이미 동기화된 깊이 안(begin >= synced_from)이면 캐시만 사용.
    fresh = state and state.synced_at and datetime.now(UTC) - state.synced_at < _SYNC_TTL
    if fresh and state.synced_from is not None and begin >= state.synced_from:
        return 0

    session = requests.Session()
    ensure_corp_mappings(db, settings, session)

    corp_code = db.scalar(
        select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == stock_code)
    )
    if not corp_code:
        logger.info("no corp_code for %s", stock_code)
        _mark_synced(db, stock_code, begin)  # 비상장 등도 TTL 동안 재조회 억제
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
        # LLM 없으면 센티먼트 분류 불가 → HOLD 로 저장해 타임라인에 공시가 보이게만 한다.
        # _mark_synced 는 하지 않아 LLM 복구 시 재분류된다.
        logger.warning("no LLM (OLLAMA_API_KEY); store disclosures as HOLD for %s", stock_code)
        saved = 0
        for d in fetched:
            if d.rcept_no in existing:
                continue
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
                    sentiment=Sentiment.HOLD,
                    rationale="LLM 미사용으로 자동 HOLD 처리",
                )
                .on_conflict_do_nothing(constraint="uq_disclosure_rcept")
            )
            db.execute(stmt)
            db.commit()
            saved += 1
        logger.info("stored %d disclosures as HOLD for %s (no LLM)", saved, stock_code)
        return saved

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

    _mark_synced(db, stock_code, begin)  # 신규 0건이어도 기록해 재조회를 억제
    logger.info("synced %d new disclosures for %s", saved, stock_code)
    return saved


def _mark_synced(db: Session, stock_code: str, begin: date) -> None:
    """동기화 시각·깊이를 기록한다. synced_from 은 더 깊은(과거) 하한만 남긴다 — 얕은 배치가
    이미 확보한 깊은 이력을 되돌리지 않도록 least(기존, 이번 begin) 로 갱신한다."""
    stmt = insert(DisclosureSyncState).values(
        stock_code=stock_code, synced_at=func.now(), synced_from=begin
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_code"],
        set_={
            "synced_at": func.now(),
            "synced_from": func.least(
                func.coalesce(DisclosureSyncState.synced_from, stmt.excluded.synced_from),
                stmt.excluded.synced_from,
            ),
        },
    )
    db.execute(stmt)
    db.commit()


# 공시 정기 배치: 유니버스를 몇 밤에 걸쳐 순환 동기화한다. 종목당 DART 콜(목록+본문)이 많아
# 한 번에 다 돌리면 IP 밴·일일한도에 걸리므로, '가장 오래 전에 동기화된' 순으로 per_run 개만
# 최근 창(_BATCH_WINDOW_DAYS)으로 갱신한다. DisclosureSyncState 를 순환 커서로 재사용
# (synced_at 오래된 것 먼저) — 온디맨드 타임라인 조회와 같은 캐시를 공유해 중복 조회를 피한다.
_BATCH_PER_RUN = 300  # 하룻밤 처리 종목 수(≈ 콜 여유). 유니버스 ~2.7천 → 약 9일 주기 전수 순환.
_BATCH_WINDOW_DAYS = 14  # 각 종목 최근 N일 공시만 조회(신규 공시 포착엔 충분).


def _batch_universe_codes(db: Session) -> list[str]:
    """정기 공시 배치 대상 — 최신 스냅샷의 상장 보통주(우선주 제외). financials 백필과 동일 필터."""
    from app.db.models import UniverseSnapshot
    from app.services import universe_ingest

    as_of = universe_ingest.latest_snapshot_date(db)
    if as_of is None:
        return []
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
        ).all()
    )


def run_disclosure_batch(
    db: Session, settings: Settings, per_run: int = _BATCH_PER_RUN
) -> dict:
    """유니버스 공시를 순환 정기 동기화한다(하룻밤 per_run 개, 오래된 것 먼저).

    반환: {synced, new, remaining}. synced=조회한 종목 수, new=신규 저장 공시 수,
    remaining=아직 이번 주기에 동기화 안 된(또는 _SYNC_TTL 지난) 종목 수.
    """
    if not settings.dart_api_key:
        logger.warning("no DART key; skip disclosure batch")
        return {"synced": 0, "new": 0, "remaining": 0}
    codes = _batch_universe_codes(db)
    if not codes:
        return {"synced": 0, "new": 0, "remaining": 0}

    # 종목별 마지막 동기화 시각 → 오래된(또는 미동기화) 순 정렬. _SYNC_TTL 안에 동기화된 건 제외.
    synced_at = dict(
        db.execute(
            select(DisclosureSyncState.stock_code, DisclosureSyncState.synced_at)
        ).all()
    )
    fresh_cut = datetime.now(UTC) - _SYNC_TTL
    pending = [c for c in codes if not (synced_at.get(c) and synced_at[c] >= fresh_cut)]
    # 미동기화(None)를 최우선, 그다음 오래된 순.
    pending.sort(key=lambda c: synced_at.get(c) or datetime.min.replace(tzinfo=UTC))
    batch = pending[:per_run]

    end = date.today()
    begin = end - timedelta(days=_BATCH_WINDOW_DAYS)
    synced = new = 0
    for code in batch:
        try:
            new += sync_disclosures(db, settings, code, begin, end)
            synced += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            logger.warning("disclosure batch failed for %s: %s", code, e)

    remaining = len(pending) - synced
    logger.info("disclosure batch: synced=%d new=%d remaining=%d", synced, new, remaining)
    return {"synced": synced, "new": new, "remaining": remaining}
