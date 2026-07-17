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
    # 내부 OllamaClient 가 OllamaError 를 던지면 어댑터는 (재시도 소진 후) LLMError 로 정규화한다
    # (서비스가 reporter 예외 타입에 결합되지 않도록).
    from app.adapters.llm import ollama as ollama_mod
    from reporter.ollama_client import OllamaError

    monkeypatch.setattr(ollama_mod.time, "sleep", lambda _s: None)  # 백오프 대기 제거
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


def test_adapter_retries_transient_then_succeeds(monkeypatch):
    # 일시적 OllamaError(타임아웃 등) 후 재시도로 성공하면 결과를 돌려준다.
    from app.adapters.llm import ollama as ollama_mod
    from reporter.ollama_client import OllamaError

    monkeypatch.setattr(ollama_mod.time, "sleep", lambda _s: None)  # 백오프 대기 제거
    adapter = OllamaLLMAdapter("https://ollama.test", "k")
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OllamaError("Read timed out")
        return "성공"

    monkeypatch.setattr(adapter._client, "chat", _flaky)
    assert adapter.chat("m", "sys", "user") == "성공"
    assert calls["n"] == 2  # 1회 실패 후 2번째 성공


def test_adapter_retries_exhausted_raises(monkeypatch):
    # 모든 재시도가 실패하면 LLMError 로 승격(마지막 오류 메시지 보존).
    from app.adapters.llm import ollama as ollama_mod
    from reporter.ollama_client import OllamaError

    monkeypatch.setattr(ollama_mod.time, "sleep", lambda _s: None)
    adapter = OllamaLLMAdapter("https://ollama.test", "k")
    calls = {"n": 0}

    def _always_down(*a, **k):
        calls["n"] += 1
        raise OllamaError("Read timed out")

    monkeypatch.setattr(adapter._client, "chat", _always_down)
    try:
        adapter.chat("m", "sys", "user")
    except LLMError as e:
        assert "timed out" in str(e)
    else:
        raise AssertionError("재시도 소진 후 LLMError 를 던져야 함")
    assert calls["n"] == ollama_mod._MAX_ATTEMPTS  # 최대 횟수만큼 시도
