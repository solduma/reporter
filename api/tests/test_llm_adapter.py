"""LLM 포트·어댑터 단위 테스트 — 팩토리 게이팅 + OllamaError→LLMError 정규화."""

from __future__ import annotations

from app.adapters.llm import OllamaLLMAdapter, get_llm
from app.config import Settings
from app.ports.llm import LLMError


def test_get_llm_none_without_key():
    # OLLAMA_API_KEY 없으면 어댑터 대신 None(호출측이 LLM 기능만 비활성).
    s = Settings(ollama_api_key="")
    assert get_llm(s) is None


def test_get_llm_returns_adapter_with_key():
    s = Settings(ollama_api_key="k", ollama_host="https://ollama.test")
    llm = get_llm(s)
    assert isinstance(llm, OllamaLLMAdapter)


def test_adapter_normalizes_ollama_error(monkeypatch):
    # 내부 OllamaClient 가 OllamaError 를 던지면 어댑터는 LLMError 로 정규화한다
    # (서비스가 reporter 예외 타입에 결합되지 않도록).
    from reporter.ollama_client import OllamaError

    adapter = OllamaLLMAdapter("https://ollama.test", "k")

    def _boom(*a, **k):
        raise OllamaError("down")

    monkeypatch.setattr(adapter._client, "chat", _boom)
    try:
        adapter.chat("m", "sys", "user")
    except LLMError:
        pass
    else:
        raise AssertionError("LLMError 로 정규화되지 않음")


def test_adapter_passes_through_content(monkeypatch):
    adapter = OllamaLLMAdapter("https://ollama.test", "k")
    monkeypatch.setattr(adapter._client, "chat", lambda *a, **k: "결과 텍스트")
    assert adapter.chat("m", "sys", "user") == "결과 텍스트"
