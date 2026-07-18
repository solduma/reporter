"""종목별 관계사(모/자회사·출자사) 수집 — DART 최대주주·타법인출자에서.

웹서치 관련성 판정 alias 원천. 전 종목 점진 백필(SyncState domain='related_company', 재개 가능)
+ 야간 cron. 종목당 DART 2콜(hyslrSttus + otrCprInvstmntSttus)이라 재무 백필보다 가볍다.
관계사명을 CorpCodeMap 로 역해석해 상장 관계사(related_stock_code)를 링크한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.adapters.dart import throttle as dart_throttle
from app.config import Settings, get_settings
from app.db.models import CorpCodeMap, RelatedCompany, SyncState, UniverseSnapshot
from app.services import sync_state, universe_ingest

logger = logging.getLogger(__name__)

_BACKFILL_DOMAIN = "related_company"
_PER_RUN = 300  # 종목당 2콜 → per_run=300 이면 하룻밤 ~600콜(한도 여유)
_YEARS_BACK = 3  # 사업보고서 연도 역순 시도 상한(최신 확정 사업연도부터)


def _corp_name_to_stock(db: Session) -> dict[str, str]:
    """corp_name → stock_code 맵(상장 관계사 역해석용). 법인명 접미사 편차는 정규화 매칭에서 흡수."""
    rows = db.execute(
        select(CorpCodeMap.corp_name, CorpCodeMap.stock_code).where(
            CorpCodeMap.stock_code.is_not(None), CorpCodeMap.stock_code != ""
        )
    ).all()
    return {_norm(n): c for n, c in rows if n}


def _norm(name: str) -> str:
    """법인명 정규화 — 공백·괄호주식회사 접미사 제거(관계사명 ↔ corp_name 매칭)."""
    s = "".join((name or "").split())
    for token in ("주식회사", "㈜", "(주)"):
        s = s.replace(token, "")
    return s


def backfill_stock(db: Session, settings: Settings, code: str, corp_map: dict[str, str]) -> bool:
    """한 종목의 관계사를 DART 에서 수집·upsert. 성공(또는 데이터없음 확정) 시 True."""
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return True  # 매핑 없음 → 완료 처리(재시도 불필요)

    today = datetime.now(UTC).date()
    related: list[dart.RelatedParty] = []
    with requests.Session() as session:
        # 최신 확정 사업연도부터 역순 — 사업보고서는 다음 해 제출이라 직전 연도부터 시도.
        for year in range(today.year - 1, today.year - 1 - _YEARS_BACK, -1):
            related = dart.fetch_related_companies(settings.dart_api_key, corp_code, year, 4, session)
            if related:
                break

    # 기존 행 삭제 후 재적재(관계사 구성 변동 반영). 종목 단위라 소량.
    db.query(RelatedCompany).filter(RelatedCompany.stock_code == code).delete()
    for rp in related:
        db.execute(
            insert(RelatedCompany)
            .values(
                stock_code=code,
                related_name=rp.name,
                relation=rp.relation,
                stake_pct=rp.stake_pct,
                related_stock_code=corp_map.get(_norm(rp.name)),
                source="hyslrSttus" if rp.relation == "parent" else "otrCprInvstmntSttus",
                bsns_year=today.year - 1,
            )
            .on_conflict_do_nothing(constraint="uq_related_company")
        )
    db.commit()
    logger.info("related company %s: %d parties", code, len(related))
    return True


def _universe_codes(db: Session) -> list[str]:
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


def _done_codes(db: Session) -> set[str]:
    return set(
        db.scalars(select(SyncState.stock_code).where(SyncState.domain == _BACKFILL_DOMAIN)).all()
    )


def run_backfill_progressive(
    db: Session, settings: Settings | None = None, per_run: int = _PER_RUN
) -> dict:
    """유니버스 종목의 관계사를 점진 백필(하룻밤 per_run 개, 재개 가능). 반환: {done, failed, budget_hit}."""
    settings = settings or get_settings()
    if not settings.dart_api_key:
        logger.warning("no DART key; skip related company backfill")
        return {"done": 0, "failed": 0, "remaining": 0}
    codes = _universe_codes(db)
    if not codes:
        return {"done": 0, "failed": 0, "remaining": 0}

    pending = [c for c in codes if c not in _done_codes(db)]
    batch = pending[:per_run]
    corp_map = _corp_name_to_stock(db)
    done = failed = 0
    quota_hit = budget_hit = False
    for code in batch:
        if dart_throttle.backfill_budget_exhausted():
            budget_hit = True
            logger.info("related company backfill: 예산 소진 — 조기 중단(%d 처리 후)", done)
            break
        try:
            if backfill_stock(db, settings, code, corp_map):
                sync_state.mark(db, _BACKFILL_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except dart.DartQuotaExceeded:
            db.rollback()
            quota_hit = True
            logger.warning("related company backfill: DART 한도초과 — 중단(%d 처리 후)", done)
            break
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("related company backfill failed for %s: %s", code, e)

    remaining = len(pending) - done
    logger.info(
        "related company backfill: done=%d failed=%d remaining=%d quota_hit=%s budget_hit=%s",
        done, failed, remaining, quota_hit, budget_hit,
    )
    return {
        "done": done, "failed": failed, "remaining": remaining,
        "quota_hit": quota_hit, "budget_hit": budget_hit,
    }


def related_names(db: Session, code: str) -> list[str]:
    """종목의 관계사명 목록(웹서치 alias·관련성 판정용)."""
    return list(
        db.scalars(select(RelatedCompany.related_name).where(RelatedCompany.stock_code == code)).all()
    )
