"""브로드캐스트 아카이브 라우터 — 텔레그램 발송 콘텐츠 열람·필터.

산업/종목/종류/기간으로 필터한다. 산업·종목 필터는 JSONB 배열 contains(@>) 로 조인한다.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import BroadcastDetail, BroadcastRef
from app.services import broadcast_ingest

router = APIRouter(prefix="/api/broadcasts", tags=["broadcasts"])

_SNIPPET_LEN = 180


@router.get("", response_model=list[BroadcastRef])
def list_broadcasts(
    industry: str | None = Query(default=None),
    stock: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[BroadcastRef]:
    try:
        rows = broadcast_ingest.list_broadcasts(
            db, industry=industry, stock=stock, kind=kind,
            from_=from_, to=to, limit=limit, offset=offset,
        )
    except broadcast_ingest.UnknownBroadcastKind:
        raise HTTPException(status_code=400, detail=f"알 수 없는 kind: {kind}") from None
    return [
        BroadcastRef(
            id=b.id,
            kind=b.kind.value,
            ref_date=b.ref_date,
            sent_at=b.sent_at,
            title=b.title,
            snippet=_snippet(b.body),
            stock_codes=b.stock_codes or [],
            industries=b.industries or [],
        )
        for b in rows
    ]


@router.get("/{broadcast_id}", response_model=BroadcastDetail)
def get_broadcast(broadcast_id: int, db: Session = Depends(get_session)) -> BroadcastDetail:
    b = broadcast_ingest.get_broadcast(db, broadcast_id)
    if not b:
        raise HTTPException(status_code=404, detail="브로드캐스트 없음")
    return BroadcastDetail(
        id=b.id,
        kind=b.kind.value,
        ref_date=b.ref_date,
        sent_at=b.sent_at,
        title=b.title,
        body=b.body,
        source_refs=b.source_refs or {},
        stock_codes=b.stock_codes or [],
        industries=b.industries or [],
    )


def _snippet(body: str) -> str:
    """헤더(첫 줄)와 구분선을 걷어낸 본문 앞부분 미리보기."""
    lines = [ln for ln in body.splitlines() if ln.strip() and set(ln.strip()) != {"─"}]
    text = " ".join(lines[1:]) if len(lines) > 1 else " ".join(lines)
    return text[:_SNIPPET_LEN] + ("…" if len(text) > _SNIPPET_LEN else "")
