"""딥다이브 오케스트레이터 — 5단계 파이프라인 실행·상태 전이·저장.

worker 가 pending job 을 잡아 run_job 을 호출한다. 단계마다 결과를 DeepDiveReport 에 저장하고 job
진행률·현재 단계를 갱신(상태폴링 대상)한다. 단계 실패는 부분 저장 후 job.failed(재개 가능). 마지막에
통합 서술 본문(narrative_md)·verdict·upside 를 만든다. LLM 미설정 시 job.failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.dart import DartQuotaExceeded
from app.adapters.llm.factory import get_llm
from app.config import Settings, get_settings
from app.db.models import DeepDiveJob, DeepDiveReport
from app.ports.llm import LLMError, LLMPort
from app.services.deepdive import hitl, stages, tools

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


def _is_stage_error(result) -> bool:
    """단계 결과가 실패·비정형 마커인가(재개 시 재실행 대상). run_stage 가 _error/_note/_partial 로 표시."""
    return isinstance(result, dict) and any(k in result for k in ("_error", "_note", "_partial"))


def _handle_hitl(
    db: Session, job: DeepDiveJob, rep: DeepDiveReport, llm: LLMPort, model: str,
    ctx: tools.ToolContext, prior: dict,
) -> bool:
    """밸류에이션 직전 HITL 처리. 진행 가능하면 True, 사용자 인풋 대기로 일시정지하면 False.

    상태 전이:
    - 아직 인풋 없음(hitl_input NULL): status=paused·hitl_pending=True·prompt 설정 → False(tick 반납).
    - 인풋 있음: 아직 미검증(rep.hitl_json 없음)이면 추가 리서치로 검증해 저장. prior['hitl'] 주입 → True.
    - 재개(이미 검증됨): 저장된 hitl_json 을 prior 에 실어 True(재검증 방지).
    """
    if job.hitl_input is None:
        # 최초 밸류에이션 도달 — 사용자에게 인풋을 청하고 멈춘다(이번 tick 반납).
        job.status = "paused"
        job.hitl_pending = True
        job.hitl_prompt = hitl.build_prompt(prior)
        db.commit()
        logger.info("deepdive paused for HITL %s (job %d)", job.stock_code, job.id)
        return False

    # 인풋 수신됨. 공백이면(사용자가 건너뜀) 검증 없이 진행. 미검증이면 추가 리서치로 검증해 저장.
    if job.hitl_input.strip() and rep.hitl_json is None:
        verdicts = hitl.verify_input(llm, model, ctx, job.hitl_input, prior)
        rep.hitl_json = verdicts
        db.commit()
    if rep.hitl_json is not None:
        prior["hitl"] = rep.hitl_json
    return True


def _get_or_create_report(
    db: Session, code: str, job_id: int, model: str, resume_from: int = 0
) -> DeepDiveReport:
    rep = db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))
    if rep is None:
        rep = DeepDiveReport(stock_code=code, job_id=job_id, model=model)
        db.add(rep)
    else:
        rep.job_id = job_id
        rep.model = model
        # 새 실행(resume_from=0): 이전 단계 결과 전부 초기화. 재개(>0): 완료 단계는 보존하고
        # 서술·verdict 등 최종 산출물만 리셋(단계 재개 후 다시 만든다).
        if resume_from <= 0:
            rep.overview_json = rep.redflags_json = rep.business_json = None
            rep.thesis_json = rep.hitl_json = rep.valuation_json = None
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

    json_cols = {
        "overview": "overview_json", "redflags": "redflags_json", "business": "business_json",
        "thesis": "thesis_json", "valuation": "valuation_json",
    }
    # 재개(좀비 회수): current_stage>0 이면 그 단계까지 완료된 것. 완료 단계 결과를 보존·재사용한다.
    resume_from = job.current_stage if job.current_stage else 0
    rep = _get_or_create_report(db, code, job.id, model, resume_from=resume_from)
    prior: dict = {}
    total = len(stages.STAGES)
    try:
        for idx, (key, fn) in enumerate(stages.STAGES, start=1):
            # 밸류에이션 직전 HITL: 사용자 인풋을 아직 안 받았으면 paused 로 멈추고 이번 tick 을 비운다.
            # 인풋을 받았으면 추가 리서치로 검증(반박/반영/가능성)해 밸류에이션 컨텍스트에 주입한다.
            if key == "valuation" and not _handle_hitl(db, job, rep, llm, model, ctx, prior):
                return  # paused — 사용자 인풋 대기(POST /hitl 로 재개)
            saved = getattr(rep, json_cols[key]) if idx <= resume_from else None
            if saved and not _is_stage_error(saved):
                prior[key] = saved  # 이미 완료된 단계 — 재계산 없이 이어받는다.
                continue
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
        .where(DeepDiveJob.stock_code == code, DeepDiveJob.status.in_(("pending", "running", "paused")))
        .order_by(DeepDiveJob.id.desc())
    )
    if existing:
        return existing
    job = DeepDiveJob(stock_code=code, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def submit_hitl(db: Session, code: str, user_input: str) -> DeepDiveJob | None:
    """paused 딥다이브에 사용자 인풋을 제출해 재개(status=pending)한다. 없으면 None.

    user_input 은 공백이어도 저장한다(= '건너뜀' 신호: 검증 없이 밸류에이션 진행). 워커가 다음 tick 에
    pending 으로 잡아 밸류에이션 직전 검증→재개한다. current_stage(=4, thesis 완료)는 보존해 재계산 없이 이어감.
    """
    job = db.scalar(
        select(DeepDiveJob)
        .where(DeepDiveJob.stock_code == code, DeepDiveJob.status == "paused")
        .order_by(DeepDiveJob.id.desc())
    )
    if job is None:
        return None
    job.hitl_input = user_input or ""
    job.hitl_pending = False
    job.status = "pending"
    db.commit()
    logger.info("deepdive HITL input received %s (job %d), resuming", code, job.id)
    return job


def latest_job(db: Session, code: str) -> DeepDiveJob | None:
    """종목의 최신 job(진행·완료 무관). 프론트 상태폴링용."""
    return db.scalar(
        select(DeepDiveJob).where(DeepDiveJob.stock_code == code).order_by(DeepDiveJob.id.desc()).limit(1)
    )


def get_report(db: Session, code: str) -> DeepDiveReport | None:
    """종목의 딥다이브 보고서(최신 1건). 없으면 None."""
    return db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))


# 정상 딥다이브는 5~15분. 이보다 오래 running 인 job 은 worker 재시작(배포)로 고아가 된 좀비로 본다.
# 단일 worker+max_instances=1 이라, 이 tick 이 도는 시점에 오래된 running 은 실행 중일 수 없다(살아있으면
# 이전 tick 이 아직 안 끝나 겹치지 않음). → 안전하게 회수해 재실행한다.
_STALE_RUNNING_MINUTES = 30


def claim_next(db: Session) -> DeepDiveJob | None:
    """처리할 job 1건 반환(worker 폴링). pending 우선, 없으면 좀비 running(배포로 고아) 회수.

    단일 worker(직렬)라 경쟁이 없어 단순 select. 다중 worker 시엔 원자적 UPDATE...RETURNING 필요.
    """
    job = db.scalar(
        select(DeepDiveJob).where(DeepDiveJob.status == "pending").order_by(DeepDiveJob.id).limit(1)
    )
    if job is not None:
        return job
    # pending 없음 → 오래 멈춘 running(worker 재시작으로 죽은 좀비) 회수. started_at 기준(NULL 도 좀비).
    cutoff = datetime.now(UTC) - timedelta(minutes=_STALE_RUNNING_MINUTES)
    stale = db.scalar(
        select(DeepDiveJob)
        .where(
            DeepDiveJob.status == "running",
            or_(DeepDiveJob.started_at.is_(None), DeepDiveJob.started_at < cutoff),
        )
        .order_by(DeepDiveJob.id)
        .limit(1)
    )
    if stale is not None:
        logger.warning("reclaiming stale running deepdive job %d (%s) — resume from stage %d",
                       stale.id, stale.stock_code, stale.current_stage)
        # current_stage 는 보존(완료 단계 이후부터 재개). status 만 pending 으로 되돌린다.
        stale.status = "pending"
        stale.started_at = None
        db.commit()
    return stale
