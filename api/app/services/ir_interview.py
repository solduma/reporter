"""주담(IR) 인터뷰 전략 — 딥다이브 밸류에이션 민감변수를 겨냥한 인터뷰 질문을 에이전틱하게 생성.

딥다이브와 독립된 후속 파이프라인. Single LLM call 이 아니라:
  (1) 밸류 민감변수 추출(코드, 결정론) — methods[].assumptions·confidence '하'·note 경고 + thesis.
  (2) 전략 아이템 도출(agent + reviewer) — 밸류 영향 큰 6~8개.
  (3) 아이템별 질문 fan-out(코드 루프, 각 reviewer 검증) — 아이템당 최대 10개.
  (4) 최대 80개 캡(코드 취합).
딥다이브의 agent(mini tool-loop)·review_loop(critique-refine)·ToolContext 를 재사용한다.
"""

from __future__ import annotations

import logging

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.llm.factory import get_llm
from app.config import Settings, get_settings
from app.db.models import DeepDiveReport, IrInterviewJob, IrInterviewReport
from app.services.deepdive import agent, review_loop, tools
from app.services.deepdive.stages import _with_feedback

logger = logging.getLogger(__name__)

_MAX_ITEMS = 8  # 전략 아이템 상한(시간·비용 관리)
_MAX_Q_PER_ITEM = 10  # 아이템당 질문 상한
_MAX_TOTAL = 80  # 전체 질문 상한(요구사항)
_REVIEW_ROUNDS = 2  # reviewer critique-refine 라운드(딥다이브 기본 3보다 낮춰 시간 관리)

# 전략 아이템 도출 reviewer — 밸류 겨냥·측정가능·커버리지 절차 감사.
_ITEMS_REVIEW = (
    "너는 주담(IR) 인터뷰 '전략 아이템' 도출의 절차 감사자다. 다음을 점검한다:\n"
    "1) 각 아이템이 밸류에이션 민감변수(목표 PER/PBR·DCF 성장률/할인율·forward EBITDA/EPS·순차입 등)나 "
    "신뢰도 낮은 방식·경고와 실제로 연결되나 — 밸류와 무관한 일반 IR 질문거리가 아님.\n"
    "2) linked_valuation_assumption 이 구체적 가정을 지목하나(막연 아님).\n"
    "3) 아이템이 성장·마진·capex·경쟁·자본배분·지배구조 등으로 골고루 퍼졌나(한 축 쏠림 아님).\n"
    "4) why_matters 가 '이 변수가 목표가를 왜 크게 움직이는지'를 설명하나."
)

# 아이템별 질문 fan-out reviewer — 답변이 가정을 좁히는가·측정가능·중복 감사.
_QUESTIONS_REVIEW = (
    "너는 주담 인터뷰 질문 배치의 절차 감사자다. 주어진 전략 아이템에 대해:\n"
    "1) 각 질문의 답변이 밸류 가정을 실제로 좁힐 수 있나 — 측정·검증 가능한 형태(수치·시점·조건)인가, "
    "예/아니오나 IR 이 답 못할 막연한 질문이 아닌가.\n"
    "2) valuation_link 가 이 아이템의 밸류 가정을 지목하나.\n"
    "3) expected_signal 이 '답변이 목표가를 어느 방향으로 움직이는지'를 명시하나.\n"
    "4) 질문끼리 중복되지 않고 아이템 하위 논점을 다양하게 커버하나."
)


def _sensitive_context(rep: DeepDiveReport) -> dict:
    """딥다이브 결과에서 밸류 민감변수 컨텍스트를 결정론적으로 추출(질문 생성 입력).

    methods[].assumptions(가정)·confidence '하'(불확실 큰 방식)·note(경고·제외사유) + entry_case·
    stock_type + thesis(drivers/risks)를 모은다. LLM 이 아이템을 여기서 겨냥한다.
    """
    val = rep.valuation_json or {}
    methods = val.get("methods") or []
    sensitive_methods = [
        {
            "method": m.get("label") or m.get("method"),
            "confidence": m.get("confidence"),
            "assumptions": m.get("assumptions"),
            "note": m.get("note"),
            "applicable": m.get("applicable"),
        }
        for m in methods
        if isinstance(m, dict)
    ]
    return {
        "stock_type": val.get("stock_type"),
        "entry_case": val.get("entry_case"),
        "final_target_price": val.get("final_target_price"),
        "final_upside_pct": val.get("final_upside_pct"),
        "methods": sensitive_methods,  # 가정·신뢰도·경고 — 질문이 겨냥할 불확실 변수
        "thesis": rep.thesis_json or {},
        "redflags": rep.redflags_json or {},
    }


def _derive_items(llm, model: str, ctx: tools.ToolContext, context: dict) -> list[dict]:
    """전략 아이템 도출(agent + reviewer). 밸류 영향 큰 불확실 가정 6~8개."""
    goal = (
        "이 종목의 밸류에이션에 가장 큰 영향을 주는 '불확실한 가정'을 겨냥해, 주담(IR)에게 물을 "
        f"인터뷰 '전략 아이템'을 최대 {_MAX_ITEMS}개 도출한다. 각 아이템은 밸류 민감변수(목표배수·"
        "성장률·할인율·forward 이익·순차입 등)나 신뢰도 낮은 밸류 방식·경고와 연결돼야 한다. "
        "성장·마진·capex·경쟁·자본배분·지배구조 등으로 폭넓게. 각 아이템에 왜 목표가를 크게 "
        "움직이는지(why_matters)와 연결된 밸류 가정(linked_valuation_assumption)을 명시한다."
    )
    schema = (
        '{"items": [{"item": "아이템명", "why_matters": "목표가에 왜 중대한지", '
        '"linked_valuation_assumption": "연결된 밸류 가정/방식"}]}'
    )
    result = review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(
            llm, model, ctx, stage_goal=_with_feedback(goal, fb),
            result_schema=schema, context_data=context, max_tool_calls=2,
        ),
        _ITEMS_REVIEW, label=f"ir_items:{ctx.code}", max_rounds=_REVIEW_ROUNDS,
    )
    items = result.get("items") if isinstance(result, dict) else None
    return [it for it in (items or []) if isinstance(it, dict) and it.get("item")][:_MAX_ITEMS]


def _questions_for_item(llm, model: str, ctx: tools.ToolContext, item: dict, context: dict) -> list[dict]:
    """한 전략 아이템의 인터뷰 질문 fan-out(agent + reviewer). 최대 _MAX_Q_PER_ITEM 개."""
    goal = (
        f"전략 아이템 '{item.get('item')}'(중요성: {item.get('why_matters')}, 연결 가정: "
        f"{item.get('linked_valuation_assumption')})에 대해, 답변이 밸류 가정을 좁힐 수 있는 주담 "
        f"인터뷰 질문을 최대 {_MAX_Q_PER_ITEM}개 만든다. 각 질문은 측정·검증 가능(수치·시점·조건)해야 "
        "하고, 왜 묻는지(intent), 어느 밸류 가정에 연결되는지(valuation_link), 답변이 목표가를 어느 "
        "방향으로 움직이는지(expected_signal)를 함께 적는다."
    )
    schema = (
        '{"questions": [{"q": "질문", "intent": "왜 묻는가", '
        '"valuation_link": "연결 밸류 가정", "expected_signal": "답변→목표가 방향"}]}'
    )
    result = review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(
            llm, model, ctx, stage_goal=_with_feedback(goal, fb),
            result_schema=schema, context_data={"item": item, **context}, max_tool_calls=2,
        ),
        _QUESTIONS_REVIEW, label=f"ir_q:{item.get('item')}:{ctx.code}", max_rounds=_REVIEW_ROUNDS,
    )
    qs = result.get("questions") if isinstance(result, dict) else None
    return [q for q in (qs or []) if isinstance(q, dict) and q.get("q")][:_MAX_Q_PER_ITEM]


def generate(db: Session, code: str, settings: Settings | None = None) -> dict:
    """주담 인터뷰 전략 생성 파이프라인. 딥다이브 밸류 결과 필요. 반환: strategy_json."""
    settings = settings or get_settings()
    llm = get_llm(settings)
    if llm is None:
        raise RuntimeError("LLM 미설정(OLLAMA_API_KEY)")
    rep = db.scalar(select(DeepDiveReport).where(DeepDiveReport.stock_code == code))
    if rep is None or not rep.valuation_json:
        raise RuntimeError("딥다이브 밸류에이션 결과가 없습니다(먼저 딥다이브 실행 필요)")

    model = settings.insight_model
    session = requests.Session()
    corp_code = tools.resolve_corp_code(db, code)
    ctx = tools.ToolContext(db=db, settings=settings, session=session, code=code, corp_code=corp_code)

    context = _sensitive_context(rep)
    items = _derive_items(llm, model, ctx, context)

    strategy: list[dict] = []
    total = 0
    for it in items:
        if total >= _MAX_TOTAL:
            break
        questions = _questions_for_item(llm, model, ctx, it, context)
        if total + len(questions) > _MAX_TOTAL:  # 전체 상한 초과분 잘라 담는다
            questions = questions[: _MAX_TOTAL - total]
        strategy.append({**it, "questions": questions})
        total += len(questions)

    return {"strategy_items": strategy, "total_questions": total}


# ── job 큐 (딥다이브와 독립) ─────────────────────────────────────────────
def enqueue(db: Session, code: str) -> IrInterviewJob:
    """주담 인터뷰 job 을 큐잉(진행 중이면 그 job 반환). worker 가 폴링해 실행."""
    existing = db.scalar(
        select(IrInterviewJob)
        .where(IrInterviewJob.stock_code == code, IrInterviewJob.status.in_(("pending", "running")))
        .order_by(IrInterviewJob.id.desc())
    )
    if existing:
        return existing
    job = IrInterviewJob(stock_code=code, status="pending")
    db.add(job)
    db.commit()
    return job


def claim_next(db: Session) -> IrInterviewJob | None:
    """가장 오래된 pending job 을 running 으로 선점(단일 워커 폴링)."""
    job = db.scalar(
        select(IrInterviewJob).where(IrInterviewJob.status == "pending").order_by(IrInterviewJob.id).limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    db.commit()
    return job


def latest_job(db: Session, code: str) -> IrInterviewJob | None:
    """종목의 최신 job(진행상태 폴링용)."""
    return db.scalar(
        select(IrInterviewJob)
        .where(IrInterviewJob.stock_code == code)
        .order_by(IrInterviewJob.id.desc())
        .limit(1)
    )


def get_report(db: Session, code: str) -> IrInterviewReport | None:
    """종목의 주담 인터뷰 전략 결과(종목당 최신 1건)."""
    return db.scalar(select(IrInterviewReport).where(IrInterviewReport.stock_code == code))


def list_reports(db: Session, limit: int = 100) -> list[IrInterviewReport]:
    """생성된 주담 전략 목록(최신순) — 최상단 메뉴 목록용."""
    return list(
        db.scalars(
            select(IrInterviewReport).order_by(IrInterviewReport.updated_at.desc()).limit(limit)
        ).all()
    )


def run_job(db: Session, job: IrInterviewJob, settings: Settings | None = None) -> None:
    """주담 인터뷰 job 실행 — 파이프라인 돌려 IrInterviewReport 에 저장. 예외는 job.failed."""
    from datetime import UTC, datetime

    job.started_at = datetime.now(UTC)
    job.progress = 5
    db.commit()
    try:
        result = generate(db, job.stock_code, settings)
        stmt = insert(IrInterviewReport).values(
            stock_code=job.stock_code, job_id=job.id, model=job.model or "",
            strategy_json=result, total_questions=result.get("total_questions", 0),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ir_interview_report_code",
            set_={
                "job_id": job.id, "strategy_json": result,
                "total_questions": result.get("total_questions", 0),
                "updated_at": func.now(), "as_of": func.now(),
            },
        )
        db.execute(stmt)
        job.status = "done"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("ir interview done %s (job %d): %d questions", job.stock_code, job.id, result.get("total_questions", 0))
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error = str(e)[:500]
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.warning("ir interview failed %s: %s", job.stock_code, e)
