"""딥다이브 최상단 배지(verdict) 조립 — 분류 · 목표가 · 업사이드."""

from __future__ import annotations

from app.services.deepdive.orchestrator import _build_verdict


def test_verdict_includes_classification_target_upside():
    # 사용자 요청: 업사이드 옆에 목표가도 함께.
    assert _build_verdict("성장주", 211078, 58.2) == "성장주 · 목표가 211,078원 · 업사이드 58%"


def test_verdict_negative_upside():
    assert _build_verdict("자산주/역발상", 7323, -20.7) == "자산주/역발상 · 목표가 7,323원 · 업사이드 -21%"


def test_verdict_omits_target_when_missing():
    # 목표가 없으면 생략(분류·업사이드만).
    assert _build_verdict("성장주", None, 24.5) == "성장주 · 업사이드 24%"


def test_verdict_omits_nonpositive_target():
    # 목표가 0/음수는 무효 → 생략.
    assert _build_verdict("성장주", 0, 24.5) == "성장주 · 업사이드 24%"


def test_verdict_entry_only():
    # 업사이드·목표가 없으면 분류만.
    assert _build_verdict("자산주/역발상", None, None) == "자산주/역발상"


def test_verdict_none_when_all_missing():
    assert _build_verdict(None, None, None) is None


def test_verdict_default_label_with_upside():
    # 분류 없어도 업사이드 있으면 '분석 · 업사이드'.
    assert _build_verdict(None, None, 12.0) == "분석 · 업사이드 12%"
