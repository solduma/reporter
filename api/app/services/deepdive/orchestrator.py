"""딥다이브 오케스트레이터 — 5단계 파이프라인 실행·상태 전이·저장.

worker 가 pending job 을 잡아 run_job 을 호출한다. 단계마다 결과를 DeepDiveReport 에 저장하고 job
진행률·현재 단계를 갱신(상태폴링 대상)한다. 단계 실패는 부분 저장 후 job.failed(재개 가능). 마지막에
통합 서술 본문(narrative_md)·verdict·upside 를 만든다. LLM 미설정 시 job.failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.dart import DartQuotaExceeded
from app.adapters.llm.factory import get_llm
from app.config import Settings, get_settings
from app.db.models import DeepDiveJob, DeepDiveReport
from app.ports.llm import LLMError, LLMPort
from app.services.deepdive import stages, tools

logger = logging.getLogger(__name__)

_NARRATIVE_SYSTEM = (
    "너는 5단계 딥다이브 분석 결과를 종합해 사람이 읽는 투자 보고서를 쓰는 애널리스트다. 각 단계 "
    "구조화 결과를 근거로, 개요→재무 특이점→사업모델→투자 아이디어·리스크→밸류에이션·결론 순의 "
    "마크다운 보고서를 쓴다. 투자 아이디어 절에서는 thesis 의 **catalysts(아직 실현 안 된 미래 촉매: 신규 "
    "수주·대형 계약·증설·인수 등 예정 이벤트)**와 **event_risks(현재 유효한 소송·유상증자·우발부채·리콜 등)**를 "
    "출처·예상 영향과 함께 짚는다(구체 이벤트가 있으면 누락 금지). 이미 종료·반영된 과거 이벤트는 서술하지 "
    "않는다. 밸류에이션은 8개 방식(PER·PBR·EV/EBITDA·DCF·DDM·"
    "자산가치·Fama-French·APT)의 목표가와 신뢰도 가중 최종 목표가(final_target_price)를 종합하되, 방식 간 "
    "편차가 크면 어느 방식을 왜 더 신뢰하는지 밝힌다. 과장 없이 데이터에 근거하고, 마지막에 한 줄 결론"
    "(투자 성격·최종 목표가·업사이드)을 남긴다."
)


def _inputs_hash(code: str, model: str) -> str:
    # 재생성 판정용(현재는 code+model+날짜). 재무·공시 갱신을 반영하려면 추후 데이터 지문 추가.
    payload = f"{code}|{model}|{datetime.now(UTC).date().isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _get_or_create_report(db: Session, code: str, job_id: int, model: str) -> DeepDiveReport:
    rep = db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))
    if rep is None:
        rep = DeepDiveReport(stock_code=code, job_id=job_id, model=model)
        db.add(rep)
    else:
        rep.job_id = job_id
        rep.model = model
        # 재실행: 이전 단계 결과 초기화(부분 잔존 방지).
        rep.overview_json = rep.redflags_json = rep.business_json = None
        rep.thesis_json = rep.valuation_json = None
        rep.narrative_md = rep.verdict = None
        rep.upside_pct = None
    db.commit()
    return rep


def run_job(db: Session, job: DeepDiveJob, settings: Settings | None = None) -> None:
    """한 딥다이브 job 을 실행(5단계). job.status 를 전이하며 진행. 예외는 job.failed 로 흡수."""
    settings = settings or get_settings()
    llm = get_llm(settings)
    if llm is None:
        _fail(db, job, "LLM 미설정(OLLAMA_API_KEY)")
        return

    model = settings.insight_model
    code = job.stock_code
    session = requests.Session()
    corp_code = tools.resolve_corp_code(db, code)
    ctx = tools.ToolContext(db=db, settings=settings, session=session, code=code, corp_code=corp_code)

    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.model = model
    db.commit()

    rep = _get_or_create_report(db, code, job.id, model)
    prior: dict = {}
    json_cols = {
        "overview": "overview_json", "redflags": "redflags_json", "business": "business_json",
        "thesis": "thesis_json", "valuation": "valuation_json",
    }
    total = len(stages.STAGES)
    try:
        for idx, (key, fn) in enumerate(stages.STAGES, start=1):
            result = fn(llm, model, ctx, prior)  # type: ignore[operator]
            prior[key] = result
            setattr(rep, json_cols[key], result)
            job.current_stage = idx
            job.progress = int(idx / (total + 1) * 100)  # +1: 마지막 서술 생성 몫
            db.commit()
        # 통합 서술 본문 + verdict/upside.
        _finalize(llm, model, code, prior, rep)
        job.progress = 100
        job.current_stage = total
        job.status = "done"
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("deepdive done %s (job %d)", code, job.id)
    except DartQuotaExceeded:
        # DART 한도초과: 불완전 데이터로 강행하지 않고 즉시 중단(재시도 매달림 방지). 자정 리셋 후 재실행.
        db.rollback()
        logger.warning("deepdive aborted (DART quota) %s", code)
        _fail(db, job, "DART 일일 조회한도 초과로 중단(자정 리셋 후 재실행). 부분 데이터로 강행 안 함.")
    except LLMError as e:
        db.rollback()
        _fail(db, job, f"LLM 오류: {e}")
    except Exception as e:  # 단계 실패 — 부분 결과는 이미 커밋됨
        db.rollback()
        logger.exception("deepdive failed %s", code)
        _fail(db, job, f"실행 오류: {e}")


def _finalize(llm: LLMPort, model: str, code: str, prior: dict, rep: DeepDiveReport) -> None:
    """5단계 결과 → 통합 마크다운 보고서 + verdict/upside. 서술 실패해도 구조화 결과는 보존."""
    val = prior.get("valuation", {}) or {}
    # 신 밸류에이션(다중 방식 blend)은 final_upside_pct, 구 스키마는 upside_pct.
    upside = val.get("final_upside_pct", val.get("upside_pct"))
    entry = val.get("entry_case")
    rep.upside_pct = float(upside) if isinstance(upside, (int, float)) else None
    if rep.upside_pct is not None:
        rep.verdict = f"{entry or '분석'} · 업사이드 {rep.upside_pct:.0f}%"
    elif entry:
        rep.verdict = str(entry)

    user = (
        f"[종목] {code}\n\n5단계 딥다이브 구조화 결과를 종합해 마크다운 보고서를 써라.\n\n"
        f"{json.dumps(prior, ensure_ascii=False)[:12000]}"
    )
    try:
        rep.narrative_md = llm.chat(model, _NARRATIVE_SYSTEM, user, temperature=0.3).strip()
    except LLMError as e:
        logger.warning("deepdive narrative failed %s: %s", code, e)
        rep.narrative_md = None
    rep.inputs_hash = _inputs_hash(code, model)
    rep.as_of = datetime.now(UTC)


def _fail(db: Session, job: DeepDiveJob, msg: str) -> None:
    job.status = "failed"
    job.error = msg[:1000]
    job.finished_at = datetime.now(UTC)
    db.commit()


def enqueue(db: Session, code: str) -> DeepDiveJob:
    """딥다이브 job 을 큐에 넣는다. 같은 종목 진행 중(pending|running) job 있으면 그걸 반환(중복 방지).

    종목명 해석 실패해도 enqueue 는 허용(worker 가 데이터 없으면 failed 처리). 라우터가 호출.
    """
    existing = db.scalar(
        select(DeepDiveJob)
        .where(DeepDiveJob.stock_code == code, DeepDiveJob.status.in_(("pending", "running")))
        .order_by(DeepDiveJob.id.desc())
    )
    if existing:
        return existing
    job = DeepDiveJob(stock_code=code, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def latest_job(db: Session, code: str) -> DeepDiveJob | None:
    """종목의 최신 job(진행·완료 무관). 프론트 상태폴링용."""
    return db.scalar(
        select(DeepDiveJob).where(DeepDiveJob.stock_code == code).order_by(DeepDiveJob.id.desc()).limit(1)
    )


def get_report(db: Session, code: str) -> DeepDiveReport | None:
    """종목의 딥다이브 보고서(최신 1건). 없으면 None."""
    return db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))


def claim_next(db: Session) -> DeepDiveJob | None:
    """가장 오래된 pending job 1건을 running 후보로 반환(worker 폴링). 실제 running 전이는 run_job 이.

    단일 worker(직렬)라 경쟁이 없어 단순 select. 다중 worker 시엔 원자적 UPDATE...RETURNING 필요.
    """
    return db.scalar(
        select(DeepDiveJob).where(DeepDiveJob.status == "pending").order_by(DeepDiveJob.id).limit(1)
    )
