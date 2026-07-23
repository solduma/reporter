"""딥다이브 결과의 무인증 임시 공유 — 스냅샷 생성·token 조회.

공유 생성 시점의 보고서를 payload 로 복사해 고정한다(이후 재분석돼도 링크 내용 불변). 조회는 로그인
게이트 밖 public 경로에서 token 으로만 이뤄지며, expires_at(생성+30분) 이후엔 만료로 취급한다.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DeepDiveReport, DeepDiveShare
from app.schemas import DeepDiveReportOut
from app.services import company_service
from app.services.deepdive.ontology_refs import extract_ontology_refs

SHARE_TTL = timedelta(minutes=30)


def report_to_out(rep: DeepDiveReport) -> DeepDiveReportOut:
    """DeepDiveReport ORM → 응답/스냅샷 DTO. 라우터·공유 스냅샷이 공유."""
    report_json = {
        "overview": rep.overview_json,
        "redflags": rep.redflags_json,
        "business": rep.business_json,
        "thesis": rep.thesis_json,
        "valuation": rep.valuation_json,
    }
    return DeepDiveReportOut(
        stock_code=rep.stock_code, model=rep.model,
        overview=rep.overview_json, redflags=rep.redflags_json, business=rep.business_json,
        thesis=rep.thesis_json, hitl=rep.hitl_json, valuation=rep.valuation_json,
        narrative_md=rep.narrative_md, verdict=rep.verdict, upside_pct=rep.upside_pct,
        ontology_refs=extract_ontology_refs(report_json), as_of=rep.as_of,
    )


def create_share(db: Session, code: str) -> DeepDiveShare | None:
    """종목의 최신 보고서를 스냅샷으로 굳혀 공유 행을 만든다. 보고서 없으면 None.

    payload_json 은 DeepDiveReportOut 을 mode="json" 직렬화(datetime→ISO 문자열)해 그대로 보관한다.
    """
    rep = db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))
    if rep is None:
        return None
    now = datetime.now(UTC)
    share = DeepDiveShare(
        token=secrets.token_urlsafe(32),
        stock_code=code,
        stock_name=company_service.resolve_stock_name(db, code),
        payload_json=report_to_out(rep).model_dump(mode="json"),
        expires_at=now + SHARE_TTL,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def get_valid_share(db: Session, token: str) -> DeepDiveShare | None:
    """만료 전 공유 스냅샷을 token 으로 조회. 없거나 만료면 None(만료 행은 지연 GC)."""
    share = db.scalar(select(DeepDiveShare).where(DeepDiveShare.token == token))
    if share is None:
        return None
    # DB 드라이버별 tz 표현 차이 흡수(Postgres=aware). naive 로 오면 UTC 로 해석.
    expires = share.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        return None
    return share
