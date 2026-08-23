"""사업 개요 조립 비동기 큐 — GET 캐시 미스·수동 갱신을 즉시 응답하고 worker 가 폴링 실행.

조립(map-reduce)은 소형 호출화로 수 분 소요 — 요청 스레드에서 돌리면 웹이 타임아웃한다.
business_research 오케스트레이터와 동일한 DB 폴링 큐 패턴(enqueue → worker claim → run).
결과는 BusinessOverviewCache 에 저장되므로 이 행은 lifecycle 만 관리한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.dart import DartQuotaExceeded
from app.config import Settings, get_settings
from app.db.models import BusinessAssemblyJob
from app.ports.llm import LLMError
from app.services import business_ingest

logger = logging.getLogger(__name__)

# 무료 모델의 map-reduce 전체(수 분) + 여유. stale running 은 재큐잉한다.
_STALE_RUNNING_MINUTES = 30


def _fail(db: Session, job: BusinessAssemblyJob, msg: str) -> None:
    job.status = "failed"
    job.error = msg[:1000]
    job.finished_at = datetime.now(UTC)
    db.commit()


def enqueue(db: Session, code: str) -> BusinessAssemblyJob:
    """조립 job 큐잉. 진행 중(pending|running) job 이 있으면 그것을 반환(dedup)."""
    existing = db.scalar(
        select(BusinessAssemblyJob)
        .where(
            BusinessAssemblyJob.stock_code == code,
            BusinessAssemblyJob.status.in_(("pending", "running")),
        )
        .order_by(BusinessAssemblyJob.id.desc())
    )
    if existing:
        return existing
    job = BusinessAssemblyJob(stock_code=code, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next(db: Session) -> BusinessAssemblyJob | None:
    """처리할 job 1건 반환(worker 폴링). pending 없으면 stale running 회수."""
    job = db.scalar(
        select(BusinessAssemblyJob)
        .where(BusinessAssemblyJob.status == "pending")
        .order_by(BusinessAssemblyJob.id)
        .limit(1)
    )
    if job is not None:
        return job
    cutoff = datetime.now(UTC) - timedelta(minutes=_STALE_RUNNING_MINUTES)
    stale = db.scalar(
        select(BusinessAssemblyJob)
        .where(
            BusinessAssemblyJob.status == "running",
            BusinessAssemblyJob.started_at.is_not(None),
            BusinessAssemblyJob.started_at < cutoff,
        )
        .order_by(BusinessAssemblyJob.id)
        .limit(1)
    )
    if stale is not None:
        logger.warning("reclaiming stale running assembly job %d (%s)", stale.id, stale.stock_code)
        stale.status = "pending"
        stale.started_at = None
        db.commit()
    return stale


def latest_job(db: Session, code: str) -> BusinessAssemblyJob | None:
    """종목의 최신 job — 상태 폴링용."""
    return db.scalar(
        select(BusinessAssemblyJob)
        .where(BusinessAssemblyJob.stock_code == code)
        .order_by(BusinessAssemblyJob.id.desc())
        .limit(1)
    )


def run_job(db: Session, job: BusinessAssemblyJob, settings: Settings | None = None) -> None:
    """한 조립 job 실행(business_ingest.assemble_overview map-reduce)."""
    settings = settings or get_settings()
    code = job.stock_code

    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.model = settings.insight_model
    db.commit()

    def _progress(pct: int) -> None:
        job.progress = max(job.progress, pct)
        db.commit()

    try:
        payload = business_ingest.assemble_overview(db, settings, code, progress=_progress)
        if payload is None:
            _fail(db, job, "조립 불가(사업보고서 부재 또는 LLM 미설정)")
            return
        job.progress = 100
        job.status = "done"
        job.error = None
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("business assembly done %s (job %d)", code, job.id)
    except DartQuotaExceeded:
        db.rollback()
        logger.warning("business assembly aborted (DART quota) %s", code)
        _fail(db, job, "DART 일일 조회한도 초과로 중단")
    except LLMError as e:
        db.rollback()
        _fail(db, job, f"LLM 오류: {e}")
    except business_ingest.AssemblyError as e:
        db.rollback()
        _fail(db, job, str(e))
    except Exception as e:
        db.rollback()
        logger.exception("business assembly failed %s", code)
        _fail(db, job, f"실행 오류: {e}")
