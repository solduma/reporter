"""딥다이브 도구 단위 테스트 — DART 한도초과(020)를 매핑오류로 오안내하지 않는지 검증."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.dart import DartQuotaExceeded
from app.services.deepdive import tools


def _ctx() -> tools.ToolContext:
    settings = MagicMock()
    settings.dart_api_key = "key"
    return tools.ToolContext(
        db=MagicMock(), settings=settings, session=MagicMock(), code="093320",
        corp_code="00603348",
    )


def test_recent_periodic_report_quota_note_is_not_mapping_error(monkeypatch):
    # 한도초과(020)면 '매핑/데이터 문제 아님'을 명시해 딥다이브가 다른 도구로 진행하게 안내.
    def _raise(*a, **k):
        raise DartQuotaExceeded("사용한도를 초과하였습니다.")

    monkeypatch.setattr(tools.dart, "find_periodic_report", _raise)
    result = tools.tool_recent_periodic_report(_ctx(), {})
    assert result["available"] is False
    assert "한도" in result["note"]
    assert "매핑" not in result["note"] or "아님" in result["note"]


def test_recent_periodic_report_not_found_note_distinct_from_quota(monkeypatch):
    # 진짜 보고서 없음(None)일 때는 한도초과와 다른 메시지.
    monkeypatch.setattr(tools.dart, "find_periodic_report", lambda *a, **k: None)
    result = tools.tool_recent_periodic_report(_ctx(), {})
    assert result["available"] is False
    assert "한도" not in result["note"]


def test_dispatch_wraps_quota_as_tool_error(monkeypatch):
    # financials 등 다른 도구는 dispatch 의 제너릭 except 가 한도초과를 오류 dict 로 전달.
    def _raise(ctx, args):
        raise DartQuotaExceeded("사용한도를 초과하였습니다.")

    monkeypatch.setitem(tools.TOOLS, "financials", (_raise, "재무"))
    result = tools.dispatch("financials", _ctx(), {})
    assert "error" in result
    assert "사용한도" in result["error"]
