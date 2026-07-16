"""딥다이브 HITL — 밸류에이션 직전 사용자 인풋을 추가 리서치로 검증해 차별 반영한다.

사용자가 넣은 인풋(예: "신규 대형 수주 임박", "무형자산 손상 우려")을 그대로 믿지 않고, 추가
리서치(뉴스·공시·리포트·웹)로 사실 여부를 확인한 뒤 인풋별로 판정한다:
- **반박**: 팩트가 아니거나 근거가 반대 → 밸류에이션에 반영하지 않고 반박 근거를 남긴다(확률 0).
- **반영**: 추가 팩트로 확인 → 100% 반영(가정 조정 지시, 확률 1).
- **가능성**: 확정은 아니나 개연성 있음 → 확률(0~1)을 매겨 그 비율만큼만 반영한다.

검증 결과(claims)를 밸류에이션 컨텍스트에 주입하면, LLM 밸류에이션 에이전트가 확률 가중으로
가정(성장률·목표 멀티플 등)을 조정한다. 산식은 여전히 domain.valuation 이 결정론적으로 소유한다.
"""

from __future__ import annotations

import json
import logging

from app.ports.llm import LLMPort
from app.services.deepdive import agent
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


_GOAL = (
    "사용자가 밸류에이션 직전 아래 인풋(user_input)을 제시했다. 이 인풋을 **그대로 믿지 말고** "
    "뉴스·공시·리포트·웹으로 추가 리서치해 사실 여부를 검증한다. 인풋에 여러 주장이 섞여 있으면 "
    "개별 주장(claim)으로 나눠 각각 판정한다.\n"
    "판정 기준(verdict):\n"
    "- '반박': 근거가 없거나 사실과 반대 — 밸류에이션에 반영하지 않는다(probability=0). 반박 근거를 evidence 에 남긴다.\n"
    "- '반영': 추가 팩트(공시·뉴스·리포트)로 확인됨 — 100% 반영(probability=1).\n"
    "- '가능성': 확정은 아니나 개연성이 있음 — 개연성 정도를 probability(0~1)로 측정해 그만큼만 반영한다.\n"
    "각 claim 의 valuation_impact 에는 밸류에이션 가정을 어떻게 조정할지 구체적으로 적는다"
    "(예: '이익성장률 +3%p', '목표 PER 12→15 상향', 'EV/EBITDA 목표배수 +1'). 반영은 확률 가중 전 "
    "'최대치'를 적고, 실제 반영 비율은 밸류에이션 단계가 probability 로 가중한다. 추측·과장 금지 — "
    "리서치로 확인되지 않으면 반박하거나 낮은 확률을 매긴다. 출처(뉴스 제목·공시명·URL)를 evidence 에 남긴다."
)

_SCHEMA = (
    '{"claims": [{"claim": "사용자 주장 요지", "verdict": "반박|반영|가능성", '
    '"probability": 0.0~1.0, "evidence": "추가 리서치 근거(출처 포함)", '
    '"reasoning": "이 판정의 이유", "valuation_impact": "밸류에이션 가정 조정(최대치)"}], '
    '"summary": "인풋이 밸류에이션에 미치는 순영향 요약"}'
)


def verify_input(llm: LLMPort, model: str, ctx: ToolContext, user_input: str, prior: dict) -> dict:
    """사용자 인풋을 추가 리서치로 검증 → claims(판정·확률·반영지시) 구조화 결과.

    agent.run_stage 의 mini tool-loop 를 재사용(event_search·web_search·disclosures·reports·
    fetch_web_page). 실패해도 밸류에이션을 막지 않도록 오류 마커를 그대로 반환(호출측이 우아하게 무시)."""
    context = {
        "user_input": user_input,
        "thesis": prior.get("thesis", {}),
        "business": prior.get("business", {}),
        "overview": prior.get("overview", {}),
        "redflags": prior.get("redflags", {}),
    }
    result = agent.run_stage(
        llm, model, ctx, stage_goal=_GOAL, result_schema=_SCHEMA,
        context_data=context, max_tool_calls=6,
    )
    logger.info("HITL verify %s: %s", ctx.code, json.dumps(result, ensure_ascii=False)[:500])
    return result
