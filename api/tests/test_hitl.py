"""HITL 수치형 인풋 검증 — critique-refine 루프(researcher ↔ 절차 reviewer) 단위 테스트."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.deepdive import hitl


class _ScriptedLLM:
    """chat 호출을 순서대로 스크립트로 응답. run_stage(researcher)·reviewer 둘 다 chat 을 쓴다.

    researcher 는 done 결과(JSON)를 곧장 내도록, reviewer 는 절차 판정 JSON 을 내도록 큐로 공급한다.
    """

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def chat(self, model, system, user, temperature=0.3):
        self.calls += 1
        return self._responses.pop(0) if self._responses else "{}"

    def chat_tools(self, *a, **k):  # 미사용(툴콜 안 씀)
        raise NotImplementedError


def _ctx():
    return hitl.ToolContext(db=MagicMock(), settings=MagicMock(), session=MagicMock(), code="093320")


def _researcher_done(claims: dict) -> str:
    # agent.run_stage 는 {"done":true,"result":{...}} 를 최종으로 인식.
    return json.dumps({"done": True, "result": claims})


def _numeric_claims(with_baseline: bool) -> dict:
    num = {
        "baseline": 60 if with_baseline else None, "new_value": 100, "unit": "MW",
        "delta_pct": 167 if with_baseline else None, "segment_revenue_share": 40,
        "conversion_chain": "용량→상면매출→영업이익" if with_baseline else "",
    }
    return {"claims": [{
        "claim": "IDC 100MW 증설", "claim_type": "numeric", "verdict": "가능성",
        "probability": 0.6, "evidence": "더벨 기사", "reasoning": "개연성",
        "numeric": num, "valuation_impact": "매출 성장 +X",
    }], "summary": "요약"}


def test_loop_converges_when_procedure_sound():
    # 1라운드에 baseline 갖춘 claim → reviewer procedure_sound → 즉시 반환(재작업 없음).
    llm = _ScriptedLLM([
        _researcher_done(_numeric_claims(with_baseline=True)),
        json.dumps({"procedure_sound": True, "gaps": []}),
    ])
    out = hitl.verify_input(llm, "m", _ctx(), "IDC 100MW 증설", {})
    assert out["claims"][0]["numeric"]["baseline"] == 60
    assert "_procedure_incomplete" not in out
    assert llm.calls == 2  # researcher 1 + reviewer 1


def test_loop_refines_after_gap_then_converges():
    # 1라운드 baseline 누락 → reviewer 지적 → 2라운드 baseline 채움 → sound.
    llm = _ScriptedLLM([
        _researcher_done(_numeric_claims(with_baseline=False)),
        json.dumps({"procedure_sound": False, "gaps": [
            {"claim": "IDC 100MW 증설", "missing_step": "baseline 미리서치",
             "fix_instruction": "현재 용량 MW 를 리서치하라"}]}),
        _researcher_done(_numeric_claims(with_baseline=True)),
        json.dumps({"procedure_sound": True, "gaps": []}),
    ])
    out = hitl.verify_input(llm, "m", _ctx(), "IDC 100MW 증설", {})
    assert out["claims"][0]["numeric"]["baseline"] == 60  # 재작업으로 채워짐
    assert "_procedure_incomplete" not in out
    assert llm.calls == 4  # researcher·reviewer 2쌍


def test_loop_best_effort_when_not_converged():
    # 상한(3라운드)까지 baseline 못 채우면 best-effort + _procedure_incomplete 마킹.
    responses = []
    for _ in range(hitl._MAX_ROUNDS):
        responses.append(_researcher_done(_numeric_claims(with_baseline=False)))
        responses.append(json.dumps({"procedure_sound": False, "gaps": [
            {"claim": "IDC 100MW 증설", "missing_step": "baseline",
             "fix_instruction": "현재 용량 리서치"}]}))
    llm = _ScriptedLLM(responses)
    out = hitl.verify_input(llm, "m", _ctx(), "IDC 100MW 증설", {})
    assert out["_procedure_incomplete"] is True
    assert out["_remaining_gaps"]  # 남은 절차 지적 노출(은폐 없음)
    assert llm.calls == hitl._MAX_ROUNDS * 2


def test_reviewer_parse_failure_stops_loop():
    # reviewer 응답이 비정형이면 무한루프 방지 위해 절차 통과로 간주하고 종료.
    llm = _ScriptedLLM([
        _researcher_done(_numeric_claims(with_baseline=True)),
        "리뷰가 JSON 이 아닌 잡텍스트",
    ])
    out = hitl.verify_input(llm, "m", _ctx(), "IDC 100MW", {})
    assert "_procedure_incomplete" not in out
    assert llm.calls == 2


def test_researcher_error_marker_short_circuits():
    # researcher 가 LLM 실패 마커를 내면 루프 중단·부분결과 반환(reviewer 호출 안 함).
    llm = _ScriptedLLM(["JSON 아님 — run_stage 가 _note 마커 반환"])
    out = hitl.verify_input(llm, "m", _ctx(), "IDC 100MW", {})
    assert hitl.agent_result_is_error(out)
    assert llm.calls == 1  # reviewer 호출 안 됨
