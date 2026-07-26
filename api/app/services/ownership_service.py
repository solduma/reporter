"""지분구조 조회 — Shareholder(주주 명부) + RelatedCompany(자회사·출자사) + elestock(최근 변동 캐시).

엔드포인트 GET /api/companies/{code}/ownership 의 응답 조립. 좌측 주주·우측 자회사는 DB 영속분
(야간 관계사 수집 related_company_ingest)을 읽고, 하단 최근 변동은 elestock(임원·주요주주 소유변동)을
12h 캐시(OwnershipChangeCache)해 DART 호출 빈도를 종목당 12h 1회로 제한한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import dart
from app.config import Settings
from app.db.models import (
    CorpCodeMap,
    OwnershipChangeCache,
    RelatedCompany,
    Shareholder,
)
from app.domain.disclosure import OwnershipChange
from app.schemas import (
    OwnershipChangeOut,
    OwnershipOut,
    ShareholderOut,
    SubsidiaryOut,
)

logger = logging.getLogger(__name__)

_CHANGE_CACHE_TTL = timedelta(hours=12)
_MAX_CHANGES = 15  # 하단 표 최대 행 수(최신순)


def _stock_name_map(db: Session, codes: set[str]) -> dict[str, str]:
    """stock_code → corp_name 맵(상장 주주·관계사 링크 표시명). CorpCodeMap 이 신뢰 원천."""
    if not codes:
        return {}
    rows = db.execute(
        select(CorpCodeMap.stock_code, CorpCodeMap.corp_name).where(
            CorpCodeMap.stock_code.in_(codes)
        )
    ).all()
    return {c: n for c, n in rows if n}


def _date_from_rcept_no(rcept_no: str) -> date | None:
    """접수번호 앞 8자리(YYYYMMDD) → date.

    DART rcept_no 규약이 '접수일자(YYYYMMDD) + 일련번호'라 별도 조회 없이 일자를 얻는다.
    형식이 다른 레거시 번호면 None.
    """
    try:
        return datetime.strptime((rcept_no or "")[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _build_change_payload(raw: dict[str, OwnershipChange]) -> list[dict]:
    """elestock 결과 → 캐시 저장·응답 직렬화용 payload 리스트(최신순, 상한 15)."""
    items = []
    for rcept_no, ch in raw.items():
        d = _date_from_rcept_no(rcept_no)
        items.append(
            {
                "rcept_no": rcept_no,
                "rcept_date": d.isoformat() if d else None,
                "reporter": ch.reporter,
                "position": ch.position,
                "shares_delta": ch.shares_delta,
                "shares_after": ch.shares_after,
                "reason": ch.reason,
            }
        )
    items.sort(key=lambda r: r["rcept_date"] or "", reverse=True)
    return items[:_MAX_CHANGES]


def _get_changes(
    db: Session, settings: Settings, code: str, corp_code: str | None
) -> tuple[list[OwnershipChangeOut], bool]:
    """최근 지분변동 — 캐시 우선(12h), miss 시 elestock live 조회 후 캐싱.

    반환 (changes, stale): stale=True 면 live 조회를 못 한 상태(쿼터초과/키없음)로,
    프론트가 짧게 폴링 재시도하도록 신호. 캐시가 있으면 그 값을 내보낸다.
    """
    cached = db.get(OwnershipChangeCache, code)
    if cached is not None:
        # sqlite(테스트)는 tz 없이 저장 → aware 로 맞춰 비교(business_ingest 캐시 판정과 동일).
        updated = cached.updated_at if cached.updated_at.tzinfo else cached.updated_at.replace(tzinfo=UTC)
        age = datetime.now(UTC) - updated
        if age < _CHANGE_CACHE_TTL:
            return [_change_out(p) for p in cached.payload], False

    # 캐시가 없거나 만료 — 갱신 불가 조건이면 기존 캐시라도 내보내고 stale.
    if not corp_code or not settings.dart_api_key:
        return (
            [_change_out(p) for p in (cached.payload if cached else [])],
            True,
        )

    try:
        with requests.Session() as session:
            raw = dart.fetch_ownership_changes(settings.dart_api_key, corp_code, session)
    except dart.DartQuotaExceeded:
        logger.info("ownership changes: DART quota exceeded %s", code)
        return [_change_out(p) for p in (cached.payload if cached else [])], True

    payload = _build_change_payload(raw)
    # 단일 행(stock_code PK) 캐시 — dialect 비의존적 get-or-update 로 갱신.
    if cached is None:
        db.add(OwnershipChangeCache(stock_code=code, payload=payload))
    else:
        cached.payload = payload
        cached.updated_at = datetime.now(UTC)
    db.commit()
    return [_change_out(p) for p in payload], False


def _change_out(p: dict) -> OwnershipChangeOut:
    return OwnershipChangeOut(
        rcept_no=p.get("rcept_no", ""),
        rcept_date=date.fromisoformat(p["rcept_date"]) if p.get("rcept_date") else None,
        reporter=p.get("reporter", ""),
        position=p.get("position", ""),
        shares_delta=p.get("shares_delta"),
        shares_after=p.get("shares_after"),
        reason=p.get("reason", ""),
    )


def get_ownership(db: Session, settings: Settings, code: str) -> OwnershipOut:
    """종목 지분구조 응답 조립 — 주주 명부 + 자회사·출자사 + 최근 지분변동."""
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))

    shareholders = (
        db.scalars(
            select(Shareholder)
            .where(Shareholder.stock_code == code)
            .order_by(Shareholder.stake_pct.desc().nullslast())
        ).all()
    )
    subsidiaries = (
        db.scalars(
            select(RelatedCompany)
            .where(
                RelatedCompany.stock_code == code,
                RelatedCompany.relation.in_(["subsidiary", "investor"]),
            )
            .order_by(RelatedCompany.stake_pct.desc().nullslast())
        ).all()
    )

    # 상장 주주·관계사 표시명 일괄 역해석.
    link_codes = {s.related_stock_code for s in shareholders if s.related_stock_code}
    link_codes |= {s.related_stock_code for s in subsidiaries if s.related_stock_code}
    names = _stock_name_map(db, link_codes)

    changes, stale = _get_changes(db, settings, code, corp_code)

    as_of_year = max((s.bsns_year for s in shareholders if s.bsns_year), default=None)

    return OwnershipOut(
        stock_code=code,
        as_of_year=as_of_year,
        shareholders=[
            ShareholderOut(
                holder_name=s.holder_name,
                relate=s.relate,
                stake_pct=s.stake_pct,
                is_corporate=s.is_corporate,
                related_stock_code=s.related_stock_code,
                related_stock_name=names.get(s.related_stock_code) if s.related_stock_code else None,
            )
            for s in shareholders
        ],
        subsidiaries=[
            SubsidiaryOut(
                related_name=s.related_name,
                relation=s.relation,
                stake_pct=s.stake_pct,
                related_stock_code=s.related_stock_code,
                related_stock_name=names.get(s.related_stock_code) if s.related_stock_code else None,
            )
            for s in subsidiaries
        ],
        changes=changes,
        changes_stale=stale,
    )
