"""딥다이브 mini tool-loop — LLMPort(단발 chat) 위에서 자율 리서치를 오케스트레이션.

포트를 바꾸지 않고, LLM 에게 도구 카탈로그를 주고 "더 필요하면 tool 을 JSON 으로 요청하라"고
지시한다. 응답을 파싱해 서비스가 도구를 코드로 실행(tools.dispatch)하고 결과를 다시 주입해 chat 을
재호출한다. LLM 이 {"done":true,"result":{...}} 를 내면 그 단계 구조화 결과 확정. 도구 호출 상한으로
비용·시간 통제. 결정론적 골격 안의 자율성 → 재현성·디버깅 유지(docs/deepdive-architecture.md).
"""

from __future__ import annotations

import json
import logging

from app.ports.llm import LLMError, LLMPort
from app.services.deepdive.tools import TOOLS, ToolContext, dispatch
from app.services.sentiment import _extract_json  # 관대 JSON 추출(코드펜스·잡텍스트 허용)

logger = logging.getLogger(__name__)


def _tools_catalog() -> str:
    """도구 목록을 프롬프트용 텍스트로. 이름 — 설명."""
    return "\n".join(f"- {name}: {desc}" for name, (_fn, desc) in TOOLS.items())


_LOOP_SYSTEM = (
    "너는 한국 주식 종목을 깊이 분석하는 리서치 애널리스트다. 주어진 단계 목표를 위해 필요한 데이터를 "
    "도구로 수집한 뒤, 근거에 기반해 구조화 결과를 만든다. 추측·과장을 피하고 데이터로 뒷받침되지 "
    "않으면 모른다고 한다.\n\n"
    "사용 가능한 도구:\n{tools}\n\n"
    "매 턴 아래 둘 중 하나의 JSON 만 출력한다(다른 텍스트 금지):\n"
    '1) 데이터가 더 필요하면: {{"tool": "도구이름", "args": {{...}}}}\n'
    '2) 충분하면 최종 결과: {{"done": true, "result": {{요구된 구조화 필드}}}}\n'
    "이미 제공된 데이터로 충분하면 곧바로 done 을 낸다. 같은 도구를 의미 없이 반복하지 않는다."
)


def run_stage(
    llm: LLMPort,
    model: str,
    ctx: ToolContext,
    *,
    stage_goal: str,
    result_schema: str,
    context_data: dict,
    max_tool_calls: int = 4,
    temperature: float = 0.2,
) -> dict:
    """한 단계의 mini tool-loop 를 돌려 구조화 결과(dict)를 반환.

    stage_goal: 이 단계가 무엇을 산출해야 하는지. result_schema: done.result 가 담아야 할 필드 설명.
    context_data: 이전 단계 결과·기본 바인딩(프롬프트 초기 컨텍스트). 상한 초과·LLM 실패 시 부분/빈 결과.
    """
    system = _LOOP_SYSTEM.format(tools=_tools_catalog())
    # 누적 대화: 초기 컨텍스트 + 단계 목표 + 결과 스키마. 도구 결과는 턴마다 append.
    transcript: list[str] = [
        f"[종목] {ctx.code}",
        f"[단계 목표]\n{stage_goal}",
        f"[최종 결과 JSON 이 담아야 할 필드]\n{result_schema}",
        f"[이미 확보된 데이터]\n{json.dumps(context_data, ensure_ascii=False)[:6000]}",
    ]
    calls = 0
    while True:
        user = "\n\n".join(transcript) + "\n\n이제 tool 요청 또는 done 결과 JSON 을 출력해라."
        try:
            raw = llm.chat(model, system, user, temperature=temperature)
        except LLMError as e:
            logger.warning("deepdive stage LLM failed %s: %s", ctx.code, e)
            return {"_error": f"LLM 실패: {e}", "_partial": True}
        data = _extract_json(raw)
        if not data:
            # JSON 이 아니면 서술 텍스트라도 result 로 회수(폴백).
            return {"_note": "비정형 응답", "_text": raw[:2000]}
        if data.get("done"):
            return data.get("result") or {}
        tool = data.get("tool")
        if not tool:
            return {"_note": "tool·done 없음", "_raw": data}
        if calls >= max_tool_calls:
            # 상한 도달 — 지금까지 수집분으로 결론을 강제한다.
            transcript.append(
                "[안내] 도구 호출 상한 도달. 추가 조회 없이 지금까지 데이터로 done 결과를 내라."
            )
            try:
                raw = llm.chat(model, system, "\n\n".join(transcript), temperature=temperature)
                forced = _extract_json(raw)
                return (forced or {}).get("result") or forced or {"_note": "상한 후 결과 없음"}
            except LLMError as e:
                return {"_error": f"상한 후 LLM 실패: {e}", "_partial": True}
        # 도구 실행 → 결과 주입
        result = dispatch(str(tool), ctx, data.get("args") or {})
        calls += 1
        snippet = json.dumps(result, ensure_ascii=False)[:5000]
        transcript.append(f"[도구 {tool} 결과]\n{snippet}")
