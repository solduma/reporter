"""분석 코멘트 캐시 단위 테스트 — 해시 안정성·캐시 히트·백그라운드 생성·중복 가드."""

from __future__ import annotations

import pytest

from app.services import analysis_comment


@pytest.fixture(autouse=True)
def _clear_inflight():
    analysis_comment._inflight.clear()
    yield
    analysis_comment._inflight.clear()


_AXES = [
    {"key": "growth", "label": "성장", "score": 70, "metrics": [{"label": "매출 YoY", "value": "+20%"}]},
    {"key": "technical", "label": "기술", "score": 55, "metrics": [{"label": "이평", "value": "예"}]},
]


def test_inputs_hash_deterministic_and_sensitive():
    h1 = analysis_comment.inputs_hash(_AXES)
    h2 = analysis_comment.inputs_hash(_AXES)
    assert h1 == h2  # 같은 입력 → 같은 해시
    changed = [dict(_AXES[0], score=71), _AXES[1]]
    assert analysis_comment.inputs_hash(changed) != h1  # 점수 바뀌면 무효화


class _Row:
    def __init__(self, h, comment):
        self.inputs_hash = h
        self.comment = comment


class _FakeDB:
    def __init__(self, row=None):
        self._row = row

    def scalar(self, stmt):
        return self._row


def test_get_cached_hit_when_hash_matches():
    h = analysis_comment.inputs_hash(_AXES)
    db = _FakeDB(_Row(h, "종합 코멘트"))
    assert analysis_comment.get_cached(db, "005930", h) == "종합 코멘트"


def test_get_cached_miss_when_hash_differs():
    db = _FakeDB(_Row("oldhash", "옛 코멘트"))
    assert analysis_comment.get_cached(db, "005930", "newhash") is None


def test_get_cached_miss_when_absent():
    assert analysis_comment.get_cached(_FakeDB(None), "005930", "h") is None


def test_generate_and_store_dedups_inflight(monkeypatch):
    # 이미 생성 중이면 LLM 을 호출하지 않는다.
    calls = {"llm": 0}
    monkeypatch.setattr(
        analysis_comment.analysis, "llm_comment",
        lambda *a, **k: calls.__setitem__("llm", calls["llm"] + 1) or "c",
    )
    h = analysis_comment.inputs_hash(_AXES)
    analysis_comment._inflight.add(f"005930|{h}")
    analysis_comment.generate_and_store("005930", "삼성", _AXES, h)
    assert calls["llm"] == 0  # 가드로 건너뜀


def test_generate_and_store_skips_store_when_no_comment(monkeypatch):
    # LLM 이 None(키 없음·실패) 반환 시 캐시하지 않는다(SessionLocal 미호출).
    monkeypatch.setattr(analysis_comment.analysis, "llm_comment", lambda *a, **k: None)
    called = {"session": 0}
    monkeypatch.setattr(
        analysis_comment, "SessionLocal",
        lambda: called.__setitem__("session", called["session"] + 1),
    )
    analysis_comment.generate_and_store("005930", "삼성", _AXES, "h")
    assert called["session"] == 0
