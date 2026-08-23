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


def _is_rate_limit(e: LLMError) -> bool:
    """429 계열(rate limit) 판정 — 어댑터가 상태·본문을 메시지에 포함하므로 문자열 매칭으로 충분."""
    msg = str(e)
    return "429" in msg or "Too Many Requests" in msg or "Rate limit" in msg


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
    """처리할 job 1건을 클레임(running 전이까지 커밋)해 반환.

    - stale 회수는 pending 존재와 무관하게 먼저 본다 — 재배포 등으로 고아된 running 이
      pending 이 남아 있는 동안 영구 방치되는 사례(060230)를 막는다. heartbeat(_progress 가
      started_at 을 갱신)로 장시간 정상 실행과 진짜 고아를 구분한다.
    - 클레임 시 즉시 running 으로 커밋한다 — 이후 폴링 틱이 같은 pending 행을 재선택해
      실행기 대기열에 중복 제출되는 이중 실행(005930 3회 실행 사례)을 원천 차단.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=_STALE_RUNNING_MINUTES)
    stale = db.scalars(
        select(BusinessAssemblyJob).where(
            BusinessAssemblyJob.status == "running",
            BusinessAssemblyJob.started_at.is_not(None),
            BusinessAssemblyJob.started_at < cutoff,
        )
    ).all()
    if stale:
        for job in stale:
            logger.warning("reclaiming stale running assembly job %d (%s)", job.id, job.stock_code)
            job.status = "pending"
            job.started_at = None
        db.commit()

    job = db.scalar(
        select(BusinessAssemblyJob)
        .where(BusinessAssemblyJob.status == "pending")
        .order_by(BusinessAssemblyJob.id)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.started_at = datetime.now(UTC)
    db.commit()
    return job


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
    if job.status in ("done", "failed"):
        # 클레임-실행 사이 이미 처리 완료된 중복 제출 방어(실행기 큐 지연 시 발생).
        logger.info("skip assembly job %d (%s=%s)", job.id, job.stock_code, job.status)
        return
    settings = settings or get_settings()
    code = job.stock_code

    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.model = settings.insight_model
    db.commit()

    def _progress(pct: int) -> None:
        job.progress = max(job.progress, pct)
        job.started_at = datetime.now(UTC)  # heartbeat — 장시간 정상 실행의 stale 회수 방지
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
        if _is_rate_limit(e):
            # 무료 tier rate limit — failed 로 남기면 수동 복구가 필요하다. pending 으로
            # 재큐잉해 한도 리셋 후 스스로 배수되게 한다(사이클당 백오프 대기가 자연 스로틀).
            job.status = "pending"
            job.started_at = None
            job.error = f"rate limit 재큐잉: {str(e)[:120]}"
            db.commit()
            logger.warning("business assembly %s rate limit — pending 재큐잉", code)
            return
        _fail(db, job, f"LLM 오류: {e}")
    except business_ingest.AssemblyError as e:
        db.rollback()
        _fail(db, job, str(e))
    except Exception as e:
        db.rollback()
        logger.exception("business assembly failed %s", code)
        _fail(db, job, f"실행 오류: {e}")
