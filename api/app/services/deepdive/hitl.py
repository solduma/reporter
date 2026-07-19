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

import logging

from app.ports.llm import LLMPort
from app.services.deepdive import agent, review_loop
from app.services.deepdive.tools import ToolContext

logger = logging.getLogger(__name__)


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
    "- 'numeric': 수치·규모 주장(용량 100MW·수주 5000억·매출 2배 등 '얼마').\n\n"
    "**[중요] 너는 플래너다 — 계산하지 마라.** 밸류에이션 이익 반영은 **코드가 실데이터로 결정론 계산**한다. "
    "너는 곱셈·나눗셈·증분율·성장률 같은 *계산 결과*를 주지 말고, 계산에 필요한 **구성요소만** 정확히 뽑아라. "
    "임의로 값을 가감·스케일·환산하면 안 된다(예: 용량 900%↑ 를 이익 증분으로 쓰지 마라).\n"
    "**numeric 은 다음 구성요소를 채운다:**\n"
    "  1) value: 인풋이 말하는 증가분의 크기. 출처의 숫자를 그대로(매출 '+50%' 면 50, 신규수주 '연 125억' 이면 125).\n"
    "  2) unit: 'pct'(비율) | 'absolute_eok'(절대 금액, 억원). value 가 %면 pct, 금액이면 absolute_eok.\n"
    "  3) target_metric: 이 증가가 어느 지표인가 — 'revenue'(매출) | 'operating_income'(영업이익) | "
    "'net_income'(순이익). 매출이 늘면 revenue(코드가 과거 증분마진으로 이익 전이 계산). 이미 이익 수치면 그 지표.\n"
    "  4) scope: value 의 적용 범위 — 'segment'(특정 사업/제품 단위 증분) | 'company'(이미 전사 기준). "
    "segment 면 segment_revenue_share(그 사업의 전체 매출 대비 비중 %)도 채운다.\n"
    "  5) conversion_chain: 근거 사슬을 서술(코드 계산 검증용). 여기에도 임의 계산값 금지, 사실·출처만.\n"
    "  절대금액은 회사 재무 단위(억원)에 맞춘다. value≤0·구성요소 부족 시 코드가 자동 미반영한다.\n\n"
    "**출처(evidence) 인정 범위: 공개 소스(뉴스·공시·리포트·웹)뿐 아니라 사용자가 IR·경영진 미팅·회사 "
    "직접 제공으로 얻은 1차 정보도 유효한 근거다. 공개 검색으로 재확인이 안 된다는 이유만으로 신뢰할 "
    "1차 출처 정보를 배제하지 마라.**\n"
    "**판정(refuted): HITL 인풋은 대개 내부정보라 공개검색으로 확인 안 되는 게 정상이다. 반박 근거"
    "(사실과 배치)를 찾았거나 인풋 자체에 논리적 모순이 있을 때만 refuted=true(미반영). 반박 못 하면 "
    "refuted=false(반영). 확률을 임의 숫자로 주지 마라 — 반영/미반영 이진 판단만 한다.**\n"
    "valuation_impact 에는 가정 조정 방향을 서술한다(계산은 코드가 함)."
)

_RESEARCH_SCHEMA = (
    '{"claims": [{"claim": "주장 요지", "claim_type": "fact_event|numeric", '
    '"refuted": false, "evidence": "리서치 근거(출처 포함). IR·회사 제공 1차 출처면 그렇게 명시", '
    '"reasoning": "판정 이유(반박 근거 유무·논리 모순 여부)", '
    '"numeric": {"value": 수|null, "unit": "pct|absolute_eok", '
    '"target_metric": "revenue|operating_income|net_income", "scope": "segment|company", '
    '"segment_revenue_share": 수|null, "conversion_chain": "근거 사슬(계산값 금지, 사실·출처만)"}, '
    '"valuation_impact": "가정 조정 방향(서술)"}], '
    '"summary": "인풋이 밸류에이션에 미치는 순영향 요약"}'
)


# ── Process-Reviewer: 절차 평가 체크리스트(공통 루프의 reviewer_system 으로 주입) ────────────
# 출력 스키마·"값 판단 금지" 철학은 review_loop 가 소유하고, 여기서는 HITL 특화 절차만 정의한다.
_REVIEW_SYSTEM = (
    "너는 딥다이브 HITL 인풋 검증의 절차 감사자다. 각 claim 에 대해 아래 절차를 점검한다:\n"
    "1) 분류 적합성: numeric(수치·규모) 주장을 fact_event 로 잘못 분류해 기준치 리서치를 생략하지 않았나.\n"
    "2) numeric 절차: baseline(현재 기준치)을 확보했나? **공개 소스로 못 구했더라도 IR 제공값·합리적 "
    "추정을 baseline 으로 쓰고 근거를 명시했으면 충족이다(공개 재확인 불가는 결함 아님). baseline 을 "
    "아예 비운 채 방치했을 때만 gap.** delta_pct 가 baseline 대비 계산됐나? conversion_chain 이 근거 있나?\n"
    "3) 근거 정합: verdict·probability 가 evidence 에 부합하나? IR·회사 제공 1차 출처는 '출처확인' "
    "verdict 로 인정 가능하며, 공개 검색이 안 된다는 이유만으로 배제·감점하는 것이 오히려 절차 오류다."
)


def _research_producer(
    llm: LLMPort, model: str, ctx: ToolContext, user_input: str, prior: dict, feedback: str | None
) -> dict:
    """Researcher 패스: tool-loop 로 리서치·구조화. feedback 있으면 이전 절차 지적을 보완하도록 재실행."""
    goal = _RESEARCH_GOAL
    if feedback:
        goal += "\n\n**[이전 검토에서 지적된 절차 미비 — 이번엔 반드시 보완하라]**\n" + feedback
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


def verify_input(llm: LLMPort, model: str, ctx: ToolContext, user_input: str, prior: dict) -> dict:
    """사용자 인풋을 critique-refine 공통 루프로 검증 → claims(판정·확률·numeric·반영지시) 구조화 결과.

    1~4단계와 동일한 review_loop 를 쓴다(HITL 특화 producer·reviewer 만 주입). 미수렴 시 마지막 결과에
    _procedure_incomplete 마킹, LLM 실패 마커는 그대로 반환(호출측이 job 실패 처리)."""
    return review_loop.run_with_review(
        llm, model,
        lambda fb: _research_producer(llm, model, ctx, user_input, prior, fb),
        _REVIEW_SYSTEM, label=f"HITL:{ctx.code}",
    )


def agent_result_is_error(result) -> bool:
    """run_stage 실패·비정형 마커(_error/_note/_partial)인가 — 호출측(orchestrator) 판정용."""
    return review_loop.result_is_error(result)
