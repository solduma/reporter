"""critique-refine 공통 루프 테스트 — producer↔reviewer 라운드·통과·미수렴·에러 조기중단."""

from __future__ import annotations

import json

from app.services.deepdive import review_loop


class _ScriptedLLM:
    """chat 호출마다 미리 준 응답을 순서대로 돌려주는 가짜 LLM(리뷰어 응답 스크립트용)."""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self.calls = 0

    def chat(self, model, system, user, temperature=0.3) -> str:
        r = self._responses[self.calls]
        self.calls += 1
        return r


def _sound() -> str:
    return json.dumps({"procedure_sound": True, "gaps": []})


def _unsound() -> str:
    return json.dumps({"procedure_sound": False, "gaps": [
        {"target": "항목A", "missing_step": "근거 누락", "fix_instruction": "출처 조사"}]})


def test_passes_on_first_sound_review():
    # producer 1회 산출 → reviewer sound → 즉시 종료(재작업 없음).
    llm = _ScriptedLLM([_sound()])
    seen_feedback = []

    def producer(fb):
        seen_feedback.append(fb)
        return {"result": "ok"}

    out = review_loop.run_with_review(llm, "m", producer, "reviewer 체크리스트")
    assert out == {"result": "ok"}
    assert seen_feedback == [None]  # 최초 라운드는 feedback 없음
    assert llm.calls == 1


def test_refines_then_passes_with_feedback_injected():
    # 1라운드 unsound → feedback 주입 → 2라운드 sound. producer 가 feedback 을 받는다.
    llm = _ScriptedLLM([_unsound(), _sound()])
    seen_feedback = []

    def producer(fb):
        seen_feedback.append(fb)
        return {"round": len(seen_feedback)}

    out = review_loop.run_with_review(llm, "m", producer, "reviewer")
    assert out == {"round": 2}
    assert seen_feedback[0] is None
    assert "출처 조사" in seen_feedback[1]  # gap 의 fix_instruction 이 feedback 으로 주입됨


def test_incomplete_marking_when_not_converged():
    # 매 라운드 unsound → 상한(_MAX_ROUNDS)까지 못 고치면 _procedure_incomplete 마킹.
    llm = _ScriptedLLM([_unsound()] * review_loop._MAX_ROUNDS)
    out = review_loop.run_with_review(llm, "m", lambda fb: {"r": 1}, "reviewer")
    assert out["_procedure_incomplete"] is True
    assert out["_remaining_gaps"]  # 남은 지적 노출(은폐 없음)
    assert llm.calls == review_loop._MAX_ROUNDS


def test_producer_error_short_circuits():
    # producer 가 에러 마커를 내면 reviewer 호출 없이 즉시 반환.
    llm = _ScriptedLLM([])  # reviewer 는 안 불림
    out = review_loop.run_with_review(llm, "m", lambda fb: {"_error": "LLM 실패", "_partial": True}, "r")
    assert review_loop.result_is_error(out)
    assert llm.calls == 0


def test_reviewer_parse_failure_stops_loop():
    # reviewer 응답이 비정형이면 무한루프 방지 위해 통과로 간주하고 종료.
    llm = _ScriptedLLM(["JSON 아닌 잡텍스트"])
    out = review_loop.run_with_review(llm, "m", lambda fb: {"r": 1}, "reviewer")
    assert "_procedure_incomplete" not in out
    assert llm.calls == 1
