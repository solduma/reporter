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
    DilutionCache,
    Financial,
    MajorHolderCache,
    OwnershipChangeCache,
    OwnershipSummary,
    RelatedCompany,
    Shareholder,
)
from app.domain.disclosure import OwnershipChange
from app.schemas import (
    DilutionOut,
    MajorHolderOut,
    OwnershipChangeOut,
    OwnershipOut,
    OwnershipSummaryOut,
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


def _classify_group(pct: float | None) -> str:
    """합산 지분율 등급 — <30% 분산 / 30~50% 안정 / 50%+ 독점."""
    if pct is None:
        return ""
    if pct >= 50:
        return "독점"
    if pct >= 30:
        return "안정"
    return "분산"


def _classify_floating(ratio: float | None) -> str:
    """유통주식 비율 등급 — <30% 과소유동 / 30~60% 적정 / 60%+ 과다유동."""
    if ratio is None:
        return ""
    if ratio >= 60:
        return "과다유동"
    if ratio >= 30:
        return "적정"
    return "과소유동"


def _compute_significance(
    sub_net_profit: int | None,
    inv_purpose: str | None,
    parent_net_income: float | None,
) -> list[str]:
    """자회사 3단계 필터 → significance 태그 목록.

    1단계 정량: 당기순이익 ≥ 모회사 10% OR 적자(당기순이익 < 0).
    2단계 리스크: 적자(otrCpr에 자본잠식/부채비율 없어 적자로 대체).
    3단계 정성: 출자목적 키워드(신사업/신규/IPO).
    """
    tags: list[str] = []
    if sub_net_profit is not None:
        if parent_net_income and parent_net_income > 0 and sub_net_profit >= parent_net_income * 0.1:
            tags.append("이익10%+")
        if sub_net_profit < 0:
            tags.append("적자")
    if inv_purpose:
        purpose_lower = inv_purpose.lower()
        for kw in ("신사업", "신규", "ipo", "신기술", "벤처"):
            if kw in purpose_lower:
                tags.append("신사업")
                break
    return tags


def _get_ownership_summary(db: Session, code: str) -> OwnershipSummaryOut | None:
    """OwnershipSummary DB 조회 → 분석 배지."""
    s = db.get(OwnershipSummary, code)
    if s is None:
        return None
    return OwnershipSummaryOut(
        group_stake_pct=s.group_stake_pct,
        group_class=_classify_group(s.group_stake_pct),
        floating_ratio=s.floating_ratio,
        floating_class=_classify_floating(s.floating_ratio),
        dilution_pct=None,  # CB/BW 희석은 _get_dilution 에서 계산
    )


def _get_major_holders(
    db: Session, settings: Settings, code: str, corp_code: str | None
) -> list[MajorHolderOut]:
    """5%+ 대량보유주주 — 캐시 우선(12h), miss 시 majorstock.json live 조회."""
    cached = db.get(MajorHolderCache, code)
    if cached is not None:
        updated = cached.updated_at if cached.updated_at.tzinfo else cached.updated_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated < _CHANGE_CACHE_TTL:
            return [MajorHolderOut(**p) for p in cached.payload]

    if not corp_code or not settings.dart_api_key:
        return [MajorHolderOut(**p) for p in (cached.payload if cached else [])]

    try:
        with requests.Session() as session:
            raw = dart.fetch_major_shareholders(settings.dart_api_key, corp_code, session)
    except dart.DartQuotaExceeded:
        logger.info("major holders: DART quota exceeded %s", code)
        return [MajorHolderOut(**p) for p in (cached.payload if cached else [])]

    payload = [
        {
            "rcept_dt": h.rcept_dt,
            "repror": h.repror,
            "stkrt": h.stkrt,
            "stkqy": h.stkqy,
            "report_resn": h.report_resn,
        }
        for h in raw
    ]
    if cached is None:
        db.add(MajorHolderCache(stock_code=code, payload=payload))
    else:
        cached.payload = payload
        cached.updated_at = datetime.now(UTC)
    db.commit()
    return [MajorHolderOut(**p) for p in payload]


def _get_dilution(
    db: Session, settings: Settings, code: str, corp_code: str | None
) -> tuple[list[DilutionOut], float | None]:
    """CB/BW 발행내역 — 캐시 우선(12h), miss 시 live 조회.

    반환 (dilution_list, dilution_pct): dilution_pct = Σ(발행주식수) / 발행주식.
    """
    cached = db.get(DilutionCache, code)
    if cached is not None:
        updated = cached.updated_at if cached.updated_at.tzinfo else cached.updated_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated < _CHANGE_CACHE_TTL:
            items = [DilutionOut(**p) for p in cached.payload]
            total_shares = sum((p.cvisstk_cnt or 0) for p in items)
            return items, _dilution_pct(total_shares, db, code)

    if not corp_code or not settings.dart_api_key:
        items = [DilutionOut(**p) for p in (cached.payload if cached else [])]
        return items, None

    # 최근 3년 CB/BW 조회.
    today = datetime.now(UTC).date()
    bgn_de = (today.replace(year=today.year - 3)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    try:
        with requests.Session() as session:
            cb_raw = dart.fetch_cb_issuance(settings.dart_api_key, corp_code, bgn_de, end_de, session)
            bw_raw = dart.fetch_bw_issuance(settings.dart_api_key, corp_code, bgn_de, end_de, session)
    except dart.DartQuotaExceeded:
        logger.info("dilution: DART quota exceeded %s", code)
        items = [DilutionOut(**p) for p in (cached.payload if cached else [])]
        return items, None

    payload = []
    for c in cb_raw:
        payload.append({
            "type": "CB",
            "bddd": c.bddd,
            "bd_fta": c.bd_fta,
            "cv_prc": c.cv_prc,
            "cvisstk_cnt": c.cvisstk_cnt,
            "tisstk_vs": c.cvisstk_tisstk_vs,
        })
    for b in bw_raw:
        payload.append({
            "type": "BW",
            "bddd": b.bddd,
            "bd_fta": b.bd_fta,
            "cv_prc": b.ex_prc,
            "cvisstk_cnt": b.nstk_isstk_cnt,
            "tisstk_vs": b.nstk_isstk_tisstk_vs,
        })
    payload.sort(key=lambda r: r["bddd"] or "", reverse=True)

    if cached is None:
        db.add(DilutionCache(stock_code=code, payload=payload))
    else:
        cached.payload = payload
        cached.updated_at = datetime.now(UTC)
    db.commit()

    items = [DilutionOut(**p) for p in payload]
    total_shares = sum((p.cvisstk_cnt or 0) for p in items)
    return items, _dilution_pct(total_shares, db, code)


def _dilution_pct(total_new_shares: int, db: Session, code: str) -> float | None:
    """잠재 희석률 = Σ(발행주식수) / 발행주식."""
    if total_new_shares <= 0:
        return None
    summary = db.get(OwnershipSummary, code)
    if summary and summary.floating_shares and summary.floating_shares > 0:
        return round(total_new_shares / summary.floating_shares * 100, 2)
    return None


def _parent_net_income(db: Session, code: str) -> float | None:
    """모회사 최신 연결(CFS) 당기순이익(억원) — 자회사 이익 10% 임계값용."""
    row = db.execute(
        select(Financial.net_income)
        .where(Financial.stock_code == code, Financial.fs_div == "CFS")
        .order_by(Financial.period.desc())
        .limit(1)
    ).scalar()
    return row


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

    # 분석 배지 — 합산 지분율·유통주식.
    summary = _get_ownership_summary(db, code)

    # 5%+ 대량보유주주.
    major_holders = _get_major_holders(db, settings, code, corp_code)

    # CB/BW 희석.
    dilution, dilution_pct = _get_dilution(db, settings, code, corp_code)
    if summary and dilution_pct is not None:
        summary.dilution_pct = dilution_pct

    # 자회사 3단계 필터 — 모회사 당기순이익.
    parent_ni = _parent_net_income(db, code)
    subsidiary_total = len(subsidiaries)
    filtered_subs: list[SubsidiaryOut] = []
    for s in subsidiaries:
        significance = _compute_significance(s.sub_net_profit, s.inv_purpose, parent_ni)
        out = SubsidiaryOut(
            related_name=s.related_name,
            relation=s.relation,
            stake_pct=s.stake_pct,
            related_stock_code=s.related_stock_code,
            related_stock_name=names.get(s.related_stock_code) if s.related_stock_code else None,
            inv_purpose=s.inv_purpose,
            book_value=s.book_value,
            sub_net_profit=s.sub_net_profit,
            significance=significance,
        )
        # significance 가 있거나 지분율 ≥ 5% 또는 장부가액 ≥ 10억이면 노출.
        if significance or (s.stake_pct is not None and s.stake_pct >= 5) or (s.book_value and s.book_value >= 1_000_000_000):
            filtered_subs.append(out)

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
        subsidiaries=filtered_subs,
        subsidiary_total=subsidiary_total,
        subsidiary_filtered=len(filtered_subs),
        changes=changes,
        changes_stale=stale,
        summary=summary,
        major_holders=major_holders,
        dilution=dilution,
    )
