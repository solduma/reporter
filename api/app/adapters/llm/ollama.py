"""OllamaLLMAdapter — LLMPort 를 Ollama Cloud(reporter.OllamaClient)로 구현.

reporter.ollama_client 직접 참조를 이 파일 한 곳으로 격리한다. chat 시그니처가 OllamaClient 와
동일해, reporter.analyzer 처럼 client 를 인자로 받는 기존 함수에 어댑터를 그대로 넘길 수 있다.
chat_tools 는 provider 의 message(dict)를 포트의 ToolTurn(구조화 tool_calls)으로 변환한다.
"""

from __future__ import annotations

import json

from app.ports.llm import LLMError, ToolCall, ToolTurn
from reporter.ollama_client import OllamaClient, OllamaError


def _parse_tool_calls(message: dict) -> list[ToolCall]:
    """provider message.tool_calls → [ToolCall]. arguments 는 dict 또는 JSON 문자열 모두 허용."""
    out: list[ToolCall] = []
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}
        out.append(ToolCall(id=str(tc.get("id") or f"call_{i}"), name=str(fn.get("name") or ""),
                            arguments=args if isinstance(args, dict) else {}))
    return out


class OllamaLLMAdapter:
    """LLMPort 구현. 내부 OllamaClient 를 감싸고 OllamaError 를 LLMError 로 정규화한다."""

    def __init__(self, host: str, api_key: str, timeout: int = 180) -> None:
        self._client = OllamaClient(host, api_key, timeout)

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        try:
            return self._client.chat(model, system, user, temperature)
        except OllamaError as e:
            raise LLMError(str(e)) from e

    def chat_tools(
        self, model: str, messages: list[dict], tools: list[dict], temperature: float = 0.2
    ) -> ToolTurn:
        try:
            message = self._client.chat_tools(model, messages, tools, temperature)
        except OllamaError as e:
            raise LLMError(str(e)) from e
        return ToolTurn(
            content=(message.get("content") or "").strip(),
            tool_calls=_parse_tool_calls(message),
            raw_message=message,
        )
