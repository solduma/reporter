"""OllamaLLMAdapter — LLMPort 를 Ollama Cloud(reporter.OllamaClient)로 구현.

reporter.ollama_client 직접 참조를 이 파일 한 곳으로 격리한다. chat 시그니처가 OllamaClient 와
동일해, reporter.analyzer 처럼 client 를 인자로 받는 기존 함수에 어댑터를 그대로 넘길 수 있다.
chat_tools 는 provider 의 message(dict)를 포트의 ToolTurn(구조화 tool_calls)으로 변환한다.
"""

from __future__ import annotations

import json
import logging
import time

from app.ports.llm import LLMError, ToolCall, ToolTurn
from reporter.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

# 일시적 실패(타임아웃·네트워크·5xx)에 대한 재시도. 긴 딥다이브/HITL 리서치 호출이 Ollama Cloud
# 부하로 간헐 타임아웃 나던 것을 흡수한다(영구 오류는 재시도해도 같으므로 소수 회로 제한).
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 2.0  # 2s, 4s 대기(지수)


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
    """LLMPort 구현. 내부 OllamaClient 를 감싸고 OllamaError 를 LLMError 로 정규화한다.

    일시적 실패(타임아웃·네트워크·부하)는 지수백오프로 재시도한다(_MAX_ATTEMPTS). 타임아웃 기본값을
    상향(300s) — 딥다이브/HITL 의 긴 리서치 프롬프트가 180s 를 넘겨 죽던 것을 완화한다.
    """

    def __init__(self, host: str, api_key: str, timeout: int = 300) -> None:
        self._client = OllamaClient(host, api_key, timeout)

    def _with_retry(self, what: str, fn):
        """fn 을 최대 _MAX_ATTEMPTS 회 시도. OllamaError 만 재시도하고, 마지막 실패는 LLMError 로 승격."""
        last: OllamaError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return fn()
            except OllamaError as e:
                last = e
                if attempt < _MAX_ATTEMPTS:
                    wait = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    logger.warning("Ollama %s 실패(시도 %d/%d): %s — %.0fs 후 재시도",
                                   what, attempt, _MAX_ATTEMPTS, e, wait)
                    time.sleep(wait)
        raise LLMError(str(last)) from last

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        return self._with_retry("chat", lambda: self._client.chat(model, system, user, temperature))

    def chat_tools(
        self, model: str, messages: list[dict], tools: list[dict], temperature: float = 0.2
    ) -> ToolTurn:
        message = self._with_retry(
            "chat_tools", lambda: self._client.chat_tools(model, messages, tools, temperature)
        )
        return ToolTurn(
            content=(message.get("content") or "").strip(),
            tool_calls=_parse_tool_calls(message),
            raw_message=message,
        )
