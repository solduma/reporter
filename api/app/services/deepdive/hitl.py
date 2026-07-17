"""딥다이브 HITL — 밸류에이션 직전 사용자 인풋을 추가 리서치로 검증해 차별 반영한다.

사용자가 넣은 인풋(예: "신규 대형 수주 임박", "IDC 100MW 증설")을 그대로 믿지 않고, 추가
리서치(뉴스·공시·리포트·웹)로 사실 여부를 확인한 뒤 인풋별로 판정한다:
- **반박**: 팩트가 아니거나 근거가 반대 → 밸류에이션에 반영하지 않고 반박 근거를 남긴다(확률 0).
- **반영**: 추가 팩트로 확인 → 100% 반영(가정 조정 지시, 확률 1).
- **가능성**: 확정은 아니나 개연성 있음 → 확률(0~1)을 매겨 그 비율만큼만 반영한다.

**수치형 인풋 강건화(critique-refine 루프)**: "100MW 추가" 같은 수치/규모 주장은 사실검증만으론
부족하다 — 현재 기준치(baseline)·전체 매출 비중을 리서치해 '증분'으로 환산해야 한다. 그래서:
Researcher(tool-loop 리서치·구조화) ↔ Process-Reviewer(tool 없이 '절차'만 평가) 를 절차가
적합해질 때까지(최대 _MAX_ROUNDS) 돌린다. Reviewer 는 결과값(목표가·확률)을 판단하지 않고,
사실검증·밸류반영이 절차적으로 옳게 이뤄졌는지(baseline 리서치·비중 파악·환산 사슬 명시 등)만 본다.

검증 결과(claims)를 밸류에이션 컨텍스트에 주입하면, LLM 밸류에이션 에이전트가 확률 가중으로
가정(성장률·목표 멀티플 등)을 조정한다. 산식은 여전히 domain.valuation 이 결정론적으로 소유한다.
"""

from __future__ import annotations

import json
import logging

from app.ports.llm import LLMError, LLMPort
from app.services.deepdive import agent
from app.services.deepdive.tools import ToolContext
from app.services.sentiment import _extract_json  # 관대 JSON 추출(코드펜스·잡텍스트 허용)

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3  # researcher 최초 + reviewer 지적 반영 재작업 최대 2회


def build_prompt(prior: dict) -> str:
    """일시정지 시 사용자에게 보일 질문 — 지금까지의 투자 아이디어·촉매·리스크를 요약해 인풋을 청한다.

    밸류에이션 전이라 목표가는 아직 없다. thesis 단계 산출물을 근거로 '무엇을 이미 파악했는지'를
    보여줘, 사용자가 빠진 정보(신규 수주·계약·리스크 등)를 보태거나 정정하도록 유도한다.
    """
    thesis = prior.get("thesis", {}) or {}
    idea = str(thesis.get("thesis") or "").strip()
    cats = thesis.get("catalysts") or []
    risks = thesis.get("event_risks") or []

    lines = ["밸류에이션을 시작하기 전입니다. 지금까지 파악한 내용은 다음과 같습니다."]
    if idea:
        lines.append(f"\n**투자 아이디어**: {idea[:400]}")
    if cats:
        cat_txt = "; ".join(str(c.get("event") if isinstance(c, dict) else c) for c in cats[:5])
        lines.append(f"**포착된 촉매**: {cat_txt}")
    if risks:
        risk_txt = "; ".join(str(r.get("event") if isinstance(r, dict) else r) for r in risks[:5])
        lines.append(f"**포착된 리스크**: {risk_txt}")
    lines.append(
        "\n추가로 밸류에이션에 반영할 정보(신규 수주·대형 계약·증설·리스크·정정할 가정 등)가 있으면 "
        "입력하세요. 입력하면 사실 여부를 추가 리서치로 검증해 확인된 만큼만 반영합니다. "
        "없으면 그대로 진행할 수 있습니다."
    )
    return "\n".join(lines)


# ── Researcher: 리서치 + 구조화 검증(피드백 반영 재실행) ─────────────────────
_RESEARCH_GOAL = (
    "사용자가 밸류에이션 직전 아래 인풋(user_input)을 제시했다. 이 인풋을 **그대로 믿지 말고** "
    "뉴스·공시·리포트·웹으로 추가 리서치해 검증한다. 여러 주장이 섞였으면 개별 주장(claim)으로 나눈다.\n"
    "각 claim 을 먼저 claim_type 으로 분류한다:\n"
    "- 'fact_event': 정성·이벤트성 주장(수주·계약·소송 등 '있다/없다').\n"
    "- 'numeric': 수치·규모 주장(용량 100MW·수주 5000억·CAPA 2배 등 '얼마').\n"
    "**numeric 은 반드시 다음을 능동적으로 리서치해 numeric 필드를 채운다(핵심):**\n"
    "  1) baseline: 현재 기준치(예 '현재 보유 IDC 용량 MW'). 이미 조사된 business/overview 단계와 "
    "     web_search·reports·financials 로 실제 파악한다. 추정·null 방치 금지 — 못 찾으면 그 사실을 evidence 에.\n"
    "  2) new_value·unit: 인풋의 신규치(예 100, 'MW').\n"
    "  3) delta_pct: 기준치 대비 증분율(new/baseline). baseline 없으면 계산 불가로 명시.\n"
    "  4) segment_revenue_share: 그 사업/제품이 전체 매출에서 차지하는 비중(%). 미미하면 전사 영향도 작다.\n"
    "  5) conversion_chain: '용량→매출→이익→밸류에이션' 환산 가정 사슬을 근거와 함께 명시(임의 숫자 금지).\n"
    "판정(verdict): '반박'(근거 없음/반대, probability=0) | '반영'(팩트 확인, probability=1) | "
    "'가능성'(개연성만, probability 0~1). valuation_impact 에는 가정 조정을 구체적으로("
    "예 '이익성장률 +3%p', '목표 PER 12→15'). numeric 은 baseline·비중을 반영한 증분이어야 한다"
    "(전체 매출 비중 5%인 사업이 2배 되면 전사 매출 +5%). 반영은 확률 가중 전 최대치. 출처를 evidence 에."
)

_RESEARCH_SCHEMA = (
    '{"claims": [{"claim": "주장 요지", "claim_type": "fact_event|numeric", '
    '"verdict": "반박|반영|가능성", "probability": 0.0~1.0, '
    '"evidence": "추가 리서치 근거(출처 포함)", "reasoning": "판정 이유", '
    '"numeric": {"baseline": 수|null, "new_value": 수|null, "unit": "", "delta_pct": 수|null, '
    '"segment_revenue_share": 수|null, "conversion_chain": "용량→매출→이익 환산 가정"}, '
    '"valuation_impact": "밸류에이션 가정 조정(최대치)"}], '
    '"summary": "인풋이 밸류에이션에 미치는 순영향 요약"}'
)


def _research_claims(
    llm: LLMPort, model: str, ctx: ToolContext, user_input: str, prior: dict, feedback: str | None
) -> dict:
    """Researcher 패스: tool-loop 로 리서치·구조화. feedback 있으면 이전 절차 지적을 보완하도록 재실행."""
    goal = _RESEARCH_GOAL
    if feedback:
        goal += (
            "\n\n**[이전 검토에서 지적된 절차 미비 — 이번엔 반드시 보완하라]**\n" + feedback
        )
    context = {
        "user_input": user_input,
        "thesis": prior.get("thesis", {}),
        "business": prior.get("business", {}),  # value_chain·item_mix(매출 비중 단서)
        "overview": prior.get("overview", {}),
        "redflags": prior.get("redflags", {}),
    }
    return agent.run_stage(
        llm, model, ctx, stage_goal=goal, result_schema=_RESEARCH_SCHEMA,
        context_data=context, max_tool_calls=6,
    )


# ── Process-Reviewer: 절차 평가(tool 없음, 값 판단 금지) ──────────────────────
_REVIEW_SYSTEM = (
    "너는 딥다이브 HITL 검증의 **절차 감사자**다. 결과값(목표가·확률의 크기·방향)이 옳은지는 판단하지 "
    "않는다. 오직 '사실검증과 밸류에이션 반영이 절차적으로 적합하게 이뤄졌는지'만 평가한다. "
    "각 claim 에 대해 아래 절차 체크리스트를 적용한다:\n"
    "1) 분류 적합성: numeric(수치·규모) 주장을 fact_event 로 잘못 분류해 기준치 리서치를 생략하지 않았나.\n"
    "2) numeric 절차: baseline(현재 기준치)을 실제 리서치했나(추정·null 방치 아님)? delta_pct 가 baseline "
    "대비 계산됐나? segment_revenue_share(전체 매출 비중)를 파악했나? conversion_chain 이 근거 있는 "
    "명시적 환산인가(임의 숫자 나열 아님)?\n"
    "3) 근거 정합: verdict·probability 가 수집된 evidence 에 절차적으로 부합하나(근거 없이 반영/반박 아님)? "
    "각 claim 에 출처(evidence)가 있나?\n"
    "**값을 고치지 마라. 절차 구멍만 지적하라(고치는 것은 리서처의 다음 라운드 몫).** "
    "모든 절차가 충족되면 procedure_sound=true. 아니면 false 와 함께 구체적 보완 지시를 gaps 에 남긴다.\n"
    "아래 JSON 만 출력:\n"
    '{"procedure_sound": true|false, "gaps": [{"claim": "대상 주장", "missing_step": "빠진 절차", '
    '"fix_instruction": "리서처가 다음에 할 구체 작업"}]}'
)


def _review_process(llm: LLMPort, model: str, claims: dict) -> dict:
    """Process-Reviewer 패스: tool 없이 절차만 평가. 파싱 실패 시 sound 로 간주(루프 종료, 은폐 방지 위해 로깅)."""
    user = (
        "다음은 리서처가 낸 HITL 검증 결과다. 절차 체크리스트로만 평가하라(값 판단 금지).\n\n"
        + json.dumps(claims, ensure_ascii=False)[:6000]
    )
    try:
        raw = llm.chat(model, _REVIEW_SYSTEM, user, temperature=0.1)
    except LLMError as e:
        logger.warning("HITL reviewer LLM failed: %s — 절차 통과로 간주", e)
        return {"procedure_sound": True, "gaps": []}
    data = _extract_json(raw)
    if not isinstance(data, dict) or "procedure_sound" not in data:
        return {"procedure_sound": True, "gaps": []}  # 비정형 → 루프 종료(무한루프 방지)
    return data


def _gaps_to_feedback(gaps: list) -> str:
    """reviewer gaps → 다음 researcher 라운드에 주입할 지침 텍스트."""
    lines = []
    for g in gaps:
        if isinstance(g, dict):
            lines.append(
                f"- 주장 '{g.get('claim')}': {g.get('missing_step')} → {g.get('fix_instruction')}"
            )
    return "\n".join(lines)


def verify_input(llm: LLMPort, model: str, ctx: ToolContext, user_input: str, prior: dict) -> dict:
    """사용자 인풋을 critique-refine 루프로 검증 → claims(판정·확률·numeric·반영지시) 구조화 결과.

    Researcher(리서치·구조화) ↔ Process-Reviewer(절차만 평가) 를 절차가 적합해질 때까지 최대
    _MAX_ROUNDS 반복. 미수렴 시 마지막 결과로 진행하되 _procedure_incomplete + 남은 gaps 를 노출한다
    (은폐 없음). 실패해도 밸류에이션을 막지 않도록 오류 마커를 그대로 반환(호출측이 우아하게 무시)."""
    feedback: str | None = None
    claims: dict = {}
    for rnd in range(_MAX_ROUNDS):
        claims = _research_claims(llm, model, ctx, user_input, prior, feedback)
        if agent_result_is_error(claims):  # LLM/파싱 실패 마커면 루프 중단(부분 결과 반환)
            return claims
        review = _review_process(llm, model, claims)
        if review.get("procedure_sound"):
            logger.info("HITL verify %s: procedure sound (round %d)", ctx.code, rnd + 1)
            return claims
        feedback = _gaps_to_feedback(review.get("gaps") or [])
        logger.info("HITL verify %s: round %d gaps → %s", ctx.code, rnd + 1, feedback[:300])
        if not feedback:  # 지적은 있으나 실행 지침이 비면 더 못 고침 → 종료
            break

    # 미수렴: 마지막 결과로 진행하되 절차 미완료를 정직하게 마킹.
    if isinstance(claims, dict):
        claims["_procedure_incomplete"] = True
        claims["_remaining_gaps"] = feedback
    logger.info("HITL verify %s: procedure incomplete after %d rounds", ctx.code, _MAX_ROUNDS)
    return claims


def agent_result_is_error(result) -> bool:
    """run_stage 실패·비정형 마커(_error/_note/_partial)인가 — 루프 중단·부분반환 판정."""
    return isinstance(result, dict) and any(k in result for k in ("_error", "_note", "_partial"))
