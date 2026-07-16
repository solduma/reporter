"""리포트 원문 발췌 보관 로직 — 상한 이하 그대로, 초과 시 요약(절단 아님)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.ports.llm import LLMError
from app.services import ingest


def test_short_text_passthrough():
    # 상한 이하면 LLM 호출 없이 원문 그대로.
    llm = MagicMock()
    text = "짧은 원문 " * 10
    out = ingest._fit_full_text(llm, "m", text)
    assert out == text
    llm.chat.assert_not_called()


def test_long_text_summarized_not_truncated():
    # 상한 초과면 LLM 요약(절단이 아니라). 종목명 보존 시스템 프롬프트로 호출.
    llm = MagicMock()
    llm.chat.return_value = "요약된 본문(종목명 보존)"
    text = "가" * (ingest._FULLTEXT_MAX_CHARS + 5000)
    out = ingest._fit_full_text(llm, "m", text)
    assert out == "요약된 본문(종목명 보존)"
    llm.chat.assert_called_once()
    # 시스템 프롬프트가 종목명 보존을 지시하는지
    sys_prompt = llm.chat.call_args[0][1]
    assert "종목명" in sys_prompt


def test_long_text_summary_capped():
    # 요약 결과가 상한을 넘어도 최종 저장분은 상한 이내.
    llm = MagicMock()
    llm.chat.return_value = "나" * (ingest._FULLTEXT_MAX_CHARS + 1000)
    out = ingest._fit_full_text(llm, "m", "다" * (ingest._FULLTEXT_MAX_CHARS + 5000))
    assert len(out) <= ingest._FULLTEXT_MAX_CHARS


def test_summary_failure_falls_back_to_truncate():
    # 요약 LLM 실패 시엔 어쩔 수 없이 절단 폴백(상한 이내).
    llm = MagicMock()
    llm.chat.side_effect = LLMError("down")
    out = ingest._fit_full_text(llm, "m", "라" * (ingest._FULLTEXT_MAX_CHARS + 5000))
    assert len(out) == ingest._FULLTEXT_MAX_CHARS
