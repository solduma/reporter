"""뉴스 이벤트 분류 단위 테스트 — LLM 응답 파싱·유형 검증(실 LLM 미사용)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services import news_events


def test_classify_parses_valid_json():
    client = MagicMock()
    client.chat.return_value = '{"event_kind": "공급망", "theme": "반도체", "summary": "미국 반도체 투자"}'
    out = news_events._classify(client, "m", "마이크론 반도체 공급망 투자", ["반도체", "2차전지"])
    assert out == {"event_kind": "공급망", "theme": "반도체", "summary": "미국 반도체 투자"}


def test_classify_invalid_kind_falls_back_to_macro():
    client = MagicMock()
    client.chat.return_value = '{"event_kind": "이상한유형", "theme": "", "summary": "x"}'
    out = news_events._classify(client, "m", "제목", ["반도체"])
    assert out["event_kind"] == "매크로"  # 유효하지 않은 유형은 매크로로


def test_classify_llm_error_returns_none():
    from app.ports.llm import LLMError

    client = MagicMock()
    client.chat.side_effect = LLMError("down")
    assert news_events._classify(client, "m", "제목", ["반도체"]) is None


def test_classify_non_json_returns_none():
    client = MagicMock()
    client.chat.return_value = "분류 불가한 자유 텍스트"
    assert news_events._classify(client, "m", "제목", ["반도체"]) is None


def test_classify_truncates_summary():
    client = MagicMock()
    long = "가" * 200
    client.chat.return_value = f'{{"event_kind": "매크로", "theme": "", "summary": "{long}"}}'
    out = news_events._classify(client, "m", "제목", [])
    assert len(out["summary"]) == 80  # 80자 절삭
