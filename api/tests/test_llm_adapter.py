"""LLM 포트·어댑터 단위 테스트 — 팩토리 게이팅 + OllamaError→LLMError 정규화."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import requests

from app.adapters.llm import OllamaLLMAdapter, get_llm
from app.config import Settings
from app.ports.llm import LLMError


def test_get_llm_none_without_key():
    # OLLAMA_API_KEY 없으면 어댑터 대신 None(호출측이 LLM 기능만 비활성).
    s = Settings(ollama_api_key="")
    assert get_llm(s) is None


def test_get_llm_returns_adapter_with_key():
    s = Settings(
        ollama_api_key="k",
        ollama_host="https://ollama.test",
        fallback_ollama_host="",   # .env 의 폴백 구성 무시 — 단일 프로바이더 케이스 검증
        fallback_ollama_api_key="",
    )
    llm = get_llm(s)
    assert isinstance(llm, OllamaLLMAdapter)


def test_get_llm_wraps_resilient_when_fallback_configured():
    s = Settings(
        ollama_api_key="k",
        ollama_host="https://primary.test",
        fallback_ollama_host="https://fallback.test",
        fallback_ollama_api_key="fk",
        fallback_insight_model="fb-model",
    )
    from app.adapters.llm.resilient import ResilientLLMAdapter

    llm = get_llm(s)
    assert isinstance(llm, ResilientLLMAdapter)
    # 주 구간: 모델명 통과 / 쿨다운(폴백 활성) 구간: 매핑된 폴백 모델명
    assert llm._map_model(s.insight_model) == s.insight_model
    llm._fallback_until = time.monotonic() + 60
    assert llm._map_model(s.insight_model) == "fb-model"


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


def test_embed_chunks_large_input(monkeypatch):
    # 수백 건 입력을 _EMBED_CHUNK 청크로 쪼개 순차 POST — 한 번에 보내면 로컬 Ollama read timeout.
    from app.adapters.llm import ollama as ollama_mod

    monkeypatch.setattr(ollama_mod, "_EMBED_CHUNK", 64)
    adapter = OllamaLLMAdapter("https://ollama.test", "k", embed_host="http://ollama.local")
    calls: list[int] = []

    def _post(url, json=None, timeout=None, **kw):
        texts = json["input"]
        calls.append(len(texts))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1] for _ in texts]}
        return resp

    monkeypatch.setattr(adapter._embed_session, "post", _post)
    out = adapter.embed("m", [f"t{i}" for i in range(150)])
    assert len(out) == 150
    assert calls == [64, 64, 22]  # 150 = 64+64+22


def test_embed_wraps_request_exception_as_llm_error(monkeypatch):
    # requests.ReadTimeout 등이 LLMError 로 정규화되어야 — 서비스가 raw requests 예외로 500 나지 않음.
    adapter = OllamaLLMAdapter("https://ollama.test", "k", embed_host="http://ollama.local")

    def _boom(*a, **k):
        raise requests.ReadTimeout("timed out")

    monkeypatch.setattr(adapter._embed_session, "post", _boom)
    try:
        adapter.embed("m", ["t1"])
    except LLMError:
        pass
    else:
        raise AssertionError("requests 예외가 LLMError 로 정규화되지 않음")
