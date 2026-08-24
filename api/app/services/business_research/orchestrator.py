"""사업 리서치 오케스트레이터 — 비동기 큐(job lifecycle) + 실행.

딥다이브 orchestrator 패턴을 재사용하되, HITL 없이 단일 스테이지(agent.run_stage)로
리서치를 수행하고, 결과를 BusinessOverviewCache.payload["research_summary"]에 병합.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.dart import DartQuotaExceeded
from app.adapters.external import _http
from app.adapters.llm.factory import get_llm
from app.config import Settings, get_settings
from app.db.models import BusinessResearchJob
from app.domain.business_research import (
    _RESEARCH_SCHEMA,
)
from app.ports.llm import LLMError
from app.services import business_ingest
from app.services.deepdive import agent, review_loop, tools

logger = logging.getLogger(__name__)

# 리서치가 오래 걸리지 않으므로 stale 임계값은 30분(딥다이브와 동일).
_STALE_RUNNING_MINUTES = 30


def _fail(db: Session, job: BusinessResearchJob, msg: str) -> None:
    """Job 실패 상태 전이."""
    job.status = "failed"
    job.error = msg[:1000]
    job.finished_at = datetime.now(UTC)
    db.commit()


def _to_research_summary(result: dict, guideline: str, model: str) -> dict:
    """LLM 산출 dict를 ResearchSummary 직렬화용 dict로 정규화."""
    # LLM이 스키마를 지키지 않을 경우 빈 값으로 안전하게 대체.
    vendors_raw = result.get("vendors") or []
    customers_raw = result.get("customers") or []
    competitors_raw = result.get("competitors") or []
    value_chain_raw = result.get("value_chain") or []
    narrative = result.get("narrative_md") or ""

    def _to_entity(raw: dict) -> dict:
        if isinstance(raw, dict):
            return {
                "name": raw.get("name", ""),
                "role": raw.get("role", ""),
                "note": raw.get("note", ""),
            }
        return {"name": "", "role": "", "note": ""}

    def _to_link(raw: dict) -> dict:
        if isinstance(raw, dict):
            return {
                "stage": raw.get("stage", ""),
                "direction": raw.get("direction", ""),
                "entity": raw.get("entity", ""),
                "note": raw.get("note", ""),
            }
        return {"stage": "", "direction": "", "entity": "", "note": ""}

    return {
        "guideline": guideline,
        "vendors": [_to_entity(e) for e in vendors_raw],
        "customers": [_to_entity(e) for e in customers_raw],
        "competitors": [_to_entity(e) for e in competitors_raw],
        "value_chain": [_to_link(e) for e in value_chain_raw],
        "narrative_md": narrative,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model,
    }


def _goal(guideline: str, feedback: str | None) -> str:
    """스테이지 목표 — 가이드라인 + review 피드백."""
    base = f"""사업 리서치를 수행해 기업의 공급망(vendors)·고객(customers)·경쟁사(competitors)·밸류체인 위치(value_chain)를 정리하고,
종합 서술(narrative_md)을 작성하라. 사용자 가이드라인: {guideline or "(없음)"}

조사 대상 종목의 사업 개요(BusinessOverviewCache)는 'business_overview' 도구로 읽을 수 있다.
필요시 web_search, fetch_web_page, disclosures, financials 등 다른 도구도 활용.
모든 정보는 출처(사업개요·공시·웹페이지 등)를 명시해야 한다.

출력은 done.result에 위 스키마를 따르는 JSON을 담아라."""
    if feedback:
        return f"""{base}

[절차 감사 피드백]
{feedback}

이 피드백을 반영해 결과를 개선하라."""
    return base


_RESEARCH_REVIEW = """리서치 결과를 절차적으로 감사하라.

체크리스트:
1. 모든 항목(vendors/customers/competitors/value_chain)에 실제 회사/법인명이 포함되어 있는가? (없으면 빈 배열 허용)
2. narrative_md에 출처(사업개요·공시·웹)를 명시했는가?
3. 할루시네이션(출처 없는 구체적 수치·사실)이 없는가?
4. 사용자 가이드라인을 충실히 반영했는가?

절차가 건전하면 procedure_sound=True, 누락/문제가 있으면 gaps:[...]로 구체적으로 지적하라.
"""


def enqueue(db: Session, code: str, guideline: str) -> BusinessResearchJob:
    """리서치 job을 큐에 넣는다. 진행 중(pending|running) job이 있으면 그걸 반환(dedup)."""
    existing = db.scalar(
        select(BusinessResearchJob)
        .where(
            BusinessResearchJob.stock_code == code,
            BusinessResearchJob.status.in_(("pending", "running")),
        )
        .order_by(BusinessResearchJob.id.desc())
    )
    if existing:
        return existing
    job = BusinessResearchJob(stock_code=code, status="pending", guideline=guideline or "")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next(db: Session) -> BusinessResearchJob | None:
    """처리할 job 1건 반환(worker 폴링)."""
    job = db.scalar(
        select(BusinessResearchJob)
        .where(BusinessResearchJob.status == "pending")
        .order_by(BusinessResearchJob.id)
        .limit(1)
    )
    if job is not None:
        return job
    # pending 없으면 오래된 running 회수.
    cutoff = datetime.now(UTC) - __import__("datetime").timedelta(minutes=_STALE_RUNNING_MINUTES)
    from sqlalchemy import or_

    stale = db.scalar(
        select(BusinessResearchJob)
        .where(
            BusinessResearchJob.status == "running",
            or_(BusinessResearchJob.started_at.is_(None), BusinessResearchJob.started_at < cutoff),
        )
        .order_by(BusinessResearchJob.id)
        .limit(1)
    )
    if stale is not None:
        logger.warning("reclaiming stale running research job %d (%s)", stale.id, stale.stock_code)
        stale.status = "pending"
        stale.started_at = None
        db.commit()
    return stale


def latest_job(db: Session, code: str) -> BusinessResearchJob | None:
    """종목의 최신 job."""
    return db.scalar(
        select(BusinessResearchJob)
        .where(BusinessResearchJob.stock_code == code)
        .order_by(BusinessResearchJob.id.desc())
        .limit(1)
    )


def run_job(db: Session, job: BusinessResearchJob, settings: Settings | None = None) -> None:
    """한 리서치 job을 실행(agent.run_stage + review_loop)."""
    settings = settings or get_settings()
    llm = get_llm(settings)
    if llm is None:
        _fail(db, job, "LLM 미설정(OLLAMA_API_KEY)")
        return

    model = settings.insight_model
    code = job.stock_code
    session = _http.resilient_session()
    corp_code = tools.resolve_corp_code(db, code)
    ctx = tools.ToolContext(
        db=db, settings=settings, session=session, code=code, corp_code=corp_code
    )

    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.model = model
    db.commit()

    try:
        # 단일 스테이지: agent.run_stage 내부에서 plan → 도구호출 → synthesize → done 한 번에.
        result = review_loop.run_with_review(
            llm,
            model,
            lambda fb: agent.run_stage(
                llm,
                model,
                ctx,
                stage_goal=_goal(job.guideline, fb),
                result_schema=_RESEARCH_SCHEMA,
                context_data={
                    "guideline": job.guideline,
                    "stock_code": code,
                    "stock_name": business_ingest.company_service.report_stock_name(db, code) or "",
                },
                max_tool_calls=6,  # business_overview + web_search + disclosures 등 여러 도구 사용 가능.
                temperature=0.3,
            ),
            _RESEARCH_REVIEW,
            label=f"bresearch:{code}",
        )
        # LLM 실패 마커(_error/_note/_partial)면 job.failed
        if review_loop.result_is_error(result):
            _fail(
                db,
                job,
                f"LLM 산출 실패: {result.get('_error') or result.get('_note') or '비정형 응답'}",
            )
            return

        # research_summary dict로 정규화 후 캐시 병합.
        summary = _to_research_summary(result, job.guideline, model)
        business_ingest._merge_research_into_cache(db, code, summary)

        job.progress = 100
        job.status = "done"
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("business research done %s (job %d)", code, job.id)
    except DartQuotaExceeded:
        db.rollback()
        logger.warning("business research aborted (DART quota) %s", code)
        _fail(db, job, "DART 일일 조회한도 초과로 중단")
    except LLMError as e:
        db.rollback()
        _fail(db, job, f"LLM 오류: {e}")
    except Exception as e:
        db.rollback()
        logger.exception("business research failed %s", code)
        _fail(db, job, f"실행 오류: {e}")
