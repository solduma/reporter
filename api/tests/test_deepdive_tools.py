"""딥다이브 도구 단위 테스트 — DART 한도초과(020)를 매핑오류로 오안내하지 않는지 검증."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.dart import DartQuotaExceeded
from app.services.deepdive import tools


def _ctx() -> tools.ToolContext:
    settings = MagicMock()
    settings.dart_api_key = "key"
    return tools.ToolContext(
        db=MagicMock(), settings=settings, session=MagicMock(), code="093320",
        corp_code="00603348",
    )


def test_recent_periodic_report_quota_aborts(monkeypatch):
    # DART 한도초과는 삼키지 않고 전파 → 딥다이브 중단(불완전 데이터로 강행·타임아웃 매달림 방지).
    def _raise(*a, **k):
        raise DartQuotaExceeded("사용한도를 초과하였습니다.")

    monkeypatch.setattr(tools.dart, "find_periodic_report", _raise)
    with pytest.raises(DartQuotaExceeded):
        tools.tool_recent_periodic_report(_ctx(), {})


def test_recent_periodic_report_not_found_note(monkeypatch):
    # 진짜 보고서 없음(None)이면 발췌 생략 안내(중단 아님).
    monkeypatch.setattr(tools.dart, "find_periodic_report", lambda *a, **k: None)
    result = tools.tool_recent_periodic_report(_ctx(), {})
    assert result["available"] is False
    assert "찾지 못" in result["note"]


def test_recent_periodic_report_caches_within_job(monkeypatch):
    # overview·business 가 같은 문서를 요청하므로 첫 조회를 ctx 캐시로 재사용(라이브 중복 제거).
    find_calls = {"n": 0}

    def _find(*a, **k):
        find_calls["n"] += 1
        return "20260101000001"  # rcept_no

    monkeypatch.setattr(tools.dart, "find_periodic_report", _find)
    monkeypatch.setattr(tools.dart, "fetch_document_text", lambda *a, **k: "본문")
    ctx = _ctx()
    first = tools.tool_recent_periodic_report(ctx, {})
    calls_after_first = find_calls["n"]
    second = tools.tool_recent_periodic_report(ctx, {})
    assert first == second and first["available"] is True
    assert find_calls["n"] == calls_after_first  # 두 번째는 DART 재조회 없음(캐시 히트)


def test_dispatch_propagates_quota_to_abort(monkeypatch):
    # DART 한도초과는 dispatch 가 오류 dict 로 삼키지 않고 전파 → run_stage→run_job 이 중단.

    def _raise(ctx, args):
        raise DartQuotaExceeded("사용한도를 초과하였습니다.")

    monkeypatch.setitem(tools.TOOLS, "financials", (_raise, "재무"))
    with pytest.raises(DartQuotaExceeded):
        tools.dispatch("financials", _ctx(), {})


def test_dispatch_wraps_nonquota_error(monkeypatch):
    # 일반 도구 오류는 여전히 오류 dict 로 흡수(루프 계속).
    def _raise(ctx, args):
        raise ValueError("일시적 파싱 실패")

    monkeypatch.setitem(tools.TOOLS, "financials", (_raise, "재무"))
    result = tools.dispatch("financials", _ctx(), {})
    assert "error" in result and "financials" in result["error"]


def test_thesis_injects_asof_date_and_forward_only(monkeypatch):
    # thesis 스테이지는 분석 기준일(as_of_date)을 컨텍스트·목표에 넣고, '이미 종료된 과거 이벤트 제외'
    # (시점 유효성)를 지시해야 한다 — 지난 촉매·리스크 반영 방지.
    from datetime import UTC, datetime

    from app.services.deepdive import stages

    captured = {}

    def _fake_run_stage(llm, model, ctx, *, stage_goal, result_schema, context_data, max_tool_calls, **kw):
        captured["goal"] = stage_goal
        captured["ctx"] = context_data
        return {}

    monkeypatch.setattr(stages.agent, "run_stage", _fake_run_stage)
    monkeypatch.setattr(stages, "dispatch", lambda n, c, a: {})
    monkeypatch.setattr(stages, "_fin_series", lambda c: [])
    # reviewer 루프는 producer(=run_stage 래퍼)를 최초 1회만 실행하게 우회(리뷰어 LLM 호출 배제).
    monkeypatch.setattr(stages.review_loop, "run_with_review",
                        lambda llm, model, producer, reviewer_system, **kw: producer(None))
    ctx = MagicMock()
    ctx.code = "093320"
    stages.stage_thesis(MagicMock(), "m", ctx, {})

    today = datetime.now(UTC).date().isoformat()
    assert captured["ctx"]["as_of_date"] == today
    assert today in captured["goal"]
    assert "이미 종료" in captured["goal"]  # 과거 이벤트 제외 지시
