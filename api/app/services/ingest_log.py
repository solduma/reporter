"""적재 배치 실행 이력 기록·조회. ingest_log 테이블의 writer + reader.

스케줄러 잡·TUI 수동 트리거가 종료 시 record() 로 1행 남긴다. 각 잡의 결과 dict 는
모양이 달라(job 별로 rows·detail 요약 규칙을 여기서 통일한다). 기록 실패가 배치 자체를
깨지 않도록 자체 세션·예외 흡수한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import IngestLog
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 잡별 표시명(TUI 이력 패널용).
JOB_LABELS = {
    "ingest_cycle": "리포트 수집",
    "nightly_batch": "야간 배치",
    "candle_batch": "봉 배치",
    "backfill_10y": "일봉 10년 백필",
    "financials_10y": "재무 10년 백필",
    "report_10y": "보고서 원문 백필",
    "news_events": "뉴스 이벤트 분류",
    "manual_ingest": "수동 리포트 수집",
    "manual_universe": "수동 유니버스",
    "manual_growth": "수동 성장 배치",
}


def _summarize(job: str, result: dict) -> tuple[int, str]:
    """잡 결과 dict → (대표 rows, 한 줄 요약). 잡마다 키가 달라 여기서 통일한다."""
    if job == "ingest_cycle":
        rows = int(result.get("reports_ingested", 0))
        return rows, (
            f"리포트 {rows} · 브로드캐스트 {result.get('broadcasts_ingested', 0)} · "
            f"30분봉 {result.get('intraday_codes', 0)}종목"
            + (" · 시황갱신" if result.get("market_brief") else "")
        )
    if job == "nightly_batch":
        rows = int(result.get("universe_rows", 0))
        # growth 는 dict({processed,total} 또는 {financials,momentum})라 대표 수치만 뽑는다.
        g = result.get("growth")
        g_n = g.get("processed", g.get("financials", 0)) if isinstance(g, dict) else (g or 0)
        return rows, f"유니버스 {rows}종목 · 성장 {g_n} · 섹터 {result.get('sectors', 0)}"
    if job == "candle_batch":
        rows = int(result.get("stocks", 0))
        return rows, (
            f"{rows}종목 (재적재 {result.get('reloaded', 0)} · "
            f"실패 {result.get('failed', 0)})"
        )
    if job in ("backfill_10y", "financials_10y"):
        rows = int(result.get("done", 0))
        return rows, (
            f"완료 {rows} · 실패 {result.get('failed', 0)} · 남음 {result.get('remaining', 0)}"
        )
    if job == "news_events":
        rows = int(result.get("events", 0))
        return rows, (
            f"뉴스 {result.get('news', 0)} · 분류 {result.get('classified', 0)} · 종목이벤트 {rows}"
        )
    # 수동 트리거 등: 결과를 그대로 문자열화.
    return 0, str(result)[:200]


def record(
    db: Session | None,
    job: str,
    result: dict | None = None,
    *,
    status: str = "ok",
    duration_ms: int = 0,
    detail: str | None = None,
    rows: int | None = None,
) -> None:
    """배치 실행 1건을 ingest_log 에 남긴다. db 가 None 이면 자체 세션을 연다.

    result 를 주면 job 규칙으로 rows·detail 을 요약한다. rows·detail 을 직접 주면 우선한다.
    기록 실패는 흡수한다(배치 결과가 이미 커밋됐을 수 있어 이력 누락이 배치를 깨면 안 됨).
    """
    own = db is None
    session = SessionLocal() if own else db
    try:
        # 요약·모델 구성도 try 안에서(결과 dict 이상값이 배치를 깨지 않게 흡수).
        if result is not None:
            sum_rows, sum_detail = _summarize(job, result)
            rows = sum_rows if rows is None else rows
            detail = sum_detail if detail is None else detail
        session.add(
            IngestLog(
                job=job, status=status, rows=rows or 0,
                detail=(detail or "")[:500], duration_ms=duration_ms,
            )
        )
        session.commit()
    except Exception as e:  # 이력 기록 실패가 배치를 깨지 않도록
        session.rollback()
        logger.warning("ingest_log record failed (%s): %s", job, e)
    finally:
        if own:
            session.close()


@dataclass
class IngestLogRow:
    ts: datetime
    job: str
    status: str
    rows: int
    detail: str
    duration_ms: int


def recent(db: Session, limit: int = 30) -> list[IngestLogRow]:
    """최근 적재 실행 이력을 최신순으로 반환한다."""
    rows = db.scalars(select(IngestLog).order_by(IngestLog.ts.desc()).limit(limit)).all()
    return [
        IngestLogRow(
            ts=r.ts, job=r.job, status=r.status, rows=r.rows, detail=r.detail, duration_ms=r.duration_ms
        )
        for r in rows
    ]


def recent_failure_count(db: Session, since_hours: int = 24) -> int:
    """최근 since_hours 시간 내 실패(status != 'ok') 배치 건수. TUI 실패 가시성 요약용."""
    since = func.now() - timedelta(hours=since_hours)
    return int(
        db.scalar(
            select(func.count())
            .select_from(IngestLog)
            .where(IngestLog.ts >= since, IngestLog.status != "ok")
        )
        or 0
    )
