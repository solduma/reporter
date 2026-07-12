"""OllamaLLMAdapter — LLMPort 를 Ollama Cloud(reporter.OllamaClient)로 구현.

reporter.ollama_client 직접 참조를 이 파일 한 곳으로 격리한다. chat 시그니처가 OllamaClient 와
동일해, reporter.analyzer 처럼 client 를 인자로 받는 기존 함수에 어댑터를 그대로 넘길 수 있다.
"""

from __future__ import annotations

from app.ports.llm import LLMError
from reporter.ollama_client import OllamaClient, OllamaError


class OllamaLLMAdapter:
    """LLMPort 구현. 내부 OllamaClient 를 감싸고 OllamaError 를 LLMError 로 정규화한다."""

    def __init__(self, host: str, api_key: str, timeout: int = 180) -> None:
        self._client = OllamaClient(host, api_key, timeout)

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        try:
            return self._client.chat(model, system, user, temperature)
        except OllamaError as e:
            raise LLMError(str(e)) from e
