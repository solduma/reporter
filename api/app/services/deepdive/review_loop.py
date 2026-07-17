"""딥다이브 critique-refine 공통 루프 — producer(리서치·산출) ↔ process-reviewer(절차 감사).

산출물의 '값'이 아니라 '절차'가 적합한지를 tool 없는 reviewer 가 감사하고, 미비하면 그 지적을
producer 에 피드백으로 주입해 절차가 적합해질 때까지(최대 max_rounds) 재작업시킨다. HITL 인풋 검증과
1~4단계 산출이 이 루프를 공유한다. 단계별로 다른 것은 (a) producer(무엇을 어떻게 산출) (b) reviewer
system(무슨 절차를 감사) 둘뿐이고, 루프 골격·피드백 주입·미수렴 마킹은 여기서 공통 소유한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from app.ports.llm import LLMError, LLMPort
from app.services.sentiment import _extract_json  # 관대 JSON 추출(코드펜스·잡텍스트 허용)

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3  # producer 최초 + reviewer 지적 반영 재작업 최대 2회

# reviewer 출력 스키마·철학(단계 무관 공통). 체크리스트 본문만 호출측이 앞에 붙인다.
# 심각도 게이트: reviewer 가 사소한 표기·출처형식까지 gap 으로 잡아 어느 단계도 통과 못 하던 문제
# (전 단계 procedure incomplete) 대응. '결론을 바꿀 중대한 결함'만 gap, 그 외엔 통과시킨다.
_REVIEW_OUTPUT_RULE = (
    "\n\n**판정 원칙(중요):**\n"
    "1) 값을 고치지 마라(목표가·확률·수치의 크기·방향 판단 금지). 절차 구멍만 본다.\n"
    "2) **결론·판단을 실제로 바꿀 만한 중대한 절차 결함만 gap 으로 지적하라.** 표기 형식·출처 문구 "
    "누락·사소한 불완전성은 gap 이 아니다(통과시켜라). 완벽이 아니라 '판단에 충분한가'가 기준이다.\n"
    "3) **이 분석 파이프라인이 접근할 수 없는 데이터**(비공개 IR·경영진 미팅 정보, 특정 공시 세부 섹션, "
    "미공개 내부수치 등)를 확보하지 못한 것은 gap 이 아니다. 공개 소스로 확인 불가한 정보라도, 신뢰할 "
    "1차 출처(예: IR·회사 직접 제공)나 합리적 추정+근거 명시가 있으면 절차 충족으로 본다. '검색으로 "
    "재확인 안 된다'는 이유만으로 배제·gap 처리하지 마라.\n"
    "4) 애매하면 통과(procedure_sound=true) 쪽으로 판정한다 — 무한 재작업보다 진행이 낫다.\n"
    "충족되면 procedure_sound=true. 중대 결함이 있을 때만 false + 구체적 보완 지시를 gaps 에. "
    "아래 JSON 만 출력:\n"
    '{"procedure_sound": true|false, "gaps": [{"target": "대상(주장·항목)", '
    '"missing_step": "빠진 절차(중대 결함만)", "fix_instruction": "producer 가 다음에 할 구체 작업"}]}'
)


def result_is_error(result) -> bool:
    """run_stage 실패·비정형 마커(_error/_note/_partial)인가 — 루프 중단·부분반환 판정."""
    return isinstance(result, dict) and any(k in result for k in ("_error", "_note", "_partial"))


def _review(llm: LLMPort, model: str, reviewer_system: str, result: dict) -> dict:
    """Process-Reviewer 패스: tool 없이 절차만 평가. 파싱 실패 시 sound 로 간주(루프 종료, 무한루프 방지)."""
    system = reviewer_system + _REVIEW_OUTPUT_RULE
    user = (
        "다음은 producer 가 낸 산출물이다. 절차 체크리스트로만 평가하라(값 판단 금지).\n\n"
        + json.dumps(result, ensure_ascii=False)[:6000]
    )
    try:
        raw = llm.chat(model, system, user, temperature=0.1)
    except LLMError as e:
        logger.warning("review LLM failed: %s — 절차 통과로 간주", e)
        return {"procedure_sound": True, "gaps": []}
    data = _extract_json(raw)
    if not isinstance(data, dict) or "procedure_sound" not in data:
        return {"procedure_sound": True, "gaps": []}  # 비정형 → 루프 종료
    return data


def _gaps_to_feedback(gaps: list) -> str:
    """reviewer gaps → 다음 producer 라운드에 주입할 지침 텍스트."""
    lines = []
    for g in gaps:
        if isinstance(g, dict):
            lines.append(
                f"- {g.get('target')}: {g.get('missing_step')} → {g.get('fix_instruction')}"
            )
    return "\n".join(lines)


def run_with_review(
    llm: LLMPort,
    model: str,
    producer: Callable[[str | None], dict],
    reviewer_system: str,
    *,
    label: str = "",
    max_rounds: int = _MAX_ROUNDS,
) -> dict:
    """producer ↔ reviewer critique-refine 루프.

    producer(feedback): feedback(이전 라운드 절차 지적, 최초 None)을 받아 산출 dict 를 낸다.
    reviewer_system: 이 산출물의 '절차'를 감사할 단계별 system 프롬프트(체크리스트).
    통과(procedure_sound)하거나 실행 지침이 빌 때까지 최대 max_rounds 회. 미수렴 시 마지막 산출물에
    _procedure_incomplete + _remaining_gaps 를 정직하게 마킹(은폐 없음). producer 가 에러 마커를 내면
    즉시 그대로 반환(호출측이 재시도·실패처리)."""
    feedback: str | None = None
    result: dict = {}
    for rnd in range(max_rounds):
        result = producer(feedback)
        if result_is_error(result):  # LLM/파싱 실패 마커면 루프 중단(부분 결과 반환)
            return result
        review = _review(llm, model, reviewer_system, result)
        if review.get("procedure_sound"):
            logger.info("review %s: procedure sound (round %d)", label, rnd + 1)
            return result
        feedback = _gaps_to_feedback(review.get("gaps") or [])
        logger.info("review %s: round %d gaps → %s", label, rnd + 1, feedback[:300])
        if not feedback:  # 지적은 있으나 실행 지침이 비면 더 못 고침 → 종료
            break

    if isinstance(result, dict):
        result["_procedure_incomplete"] = True
        result["_remaining_gaps"] = feedback
    logger.info("review %s: procedure incomplete after %d rounds", label, max_rounds)
    return result
