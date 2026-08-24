"""딥다이브 완료 요약의 저장·조회 — 인사이트 소비처(screener·comment) 연결 고리."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import InsightFeedback


def upsert_from_deepdive(db: Session, stock_code: str, report) -> None:
    """완료된 DeepDiveReport 에서 요약을 뽑아 insight_feedback 에 upsert.

    risk_count 는 redflags_json 항목 수, summary_line 은 verdict+upside 한 줄.
    실패해도 딥다이브 본 처리를 깨지 않게 호출측에서 예외를 흡수한다.
    """
    redflags = _safe_list(getattr(report, "redflags_json", None))
    verdict = getattr(report, "verdict", None)
    upside = getattr(report, "upside_pct", None)
    line = f"딥다이브({datetime.now(UTC).strftime('%Y-%m-%d')}): {verdict or '분석 완료'}"
    if redflags:
        line += f" · 리스크 플래그 {len(redflags)}건"

    stmt = insert(InsightFeedback).values(
        stock_code=stock_code,
        verdict=(verdict or "")[:200] or None,
        upside_pct=upside,
        risk_count=len(redflags),
        summary_line=line[:1000],
        updated_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_code"],
        set_={
            "verdict": stmt.excluded.verdict,
            "upside_pct": stmt.excluded.upside_pct,
            "risk_count": stmt.excluded.risk_count,
            "summary_line": stmt.excluded.summary_line,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    db.execute(stmt)
    db.commit()


def get_summary(db: Session, code: str) -> str | None:
    """코멘트 프롬프트 주입용 한 줄. 없으면 None."""
    row = db.scalar(select(InsightFeedback).where(InsightFeedback.stock_code == code))
    if row is None:
        return None
    return row.summary_line or None


def _safe_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []
