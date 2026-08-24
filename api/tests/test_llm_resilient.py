"""ResilientLLMAdapter — rate limit 시 폴백 전환·모델 매핑·쿨다운 복귀."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.adapters.llm.resilient import ResilientLLMAdapter
from app.ports.llm import LLMError


def _adapter(responses):
    """responses: 예외 인스턴스 또는 반환값 문자열을 순서대로 내보내는 chat 목."""
    m = MagicMock()
    m.chat.side_effect = list(responses)
    return m


RATE = LLMError("429 Client Error — FreeUsageLimitError: Rate limit exceeded")


def test_falls_back_immediately_on_rate_limit():
    primary = _adapter([RATE])
    fallback = _adapter(["폴백 응답"])
    r = ResilientLLMAdapter(primary, fallback, model_map={"ins": "qwen3.5:cloud"})
    out = r.chat("ins", "sys", "user")
    assert out == "폴백 응답"
    # 폴백 호출엔 매핑된 모델명이 사용됐다
    args, kwargs = fallback.chat.call_args
    assert kwargs.get("model") or args[0] == "qwen3.5:cloud"


def test_cooldown_routes_to_fallback_then_returns_primary():
    primary = _adapter([RATE, "주 복귀"])
    fallback = _adapter(["폴백1", "폴백1"])
    r = ResilientLLMAdapter(primary, fallback, cooldown_s=60)
    assert r.chat("m", "s", "u") == "폴백1"
    assert r.chat("m", "s", "u") == "폴백1"  # 쿨다운 중 → 폴백 우선
    time.sleep(0)  # 쿨다운 만료 강제
    r._fallback_until = 0.0
    assert r.chat("m", "s", "u") == "주 복귀"  # primary 복귀


def test_non_rate_limit_error_raises_without_fallback():
    primary = _adapter([LLMError("응답에 content 가 없습니다[finish=stop]")])
    fallback = _adapter(["폴백"])
    r = ResilientLLMAdapter(primary, fallback)
    with pytest.raises(LLMError):
        r.chat("m", "s", "u")
    fallback.chat.assert_not_called()


def test_embed_delegates_to_primary_only():
    primary = MagicMock()
    fallback = MagicMock()
    primary.embed.return_value = [[0.1]]
    r = ResilientLLMAdapter(primary, fallback)
    assert r.embed("e", ["t"]) == [[0.1]]
    fallback.embed.assert_not_called()
