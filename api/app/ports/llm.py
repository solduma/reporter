"""LLMPort — 텍스트 생성(chat) + 네이티브 도구호출(chat_tools) 인터페이스.

요약·분석·분류에 쓰는 LLM 호출을 이 포트에 의존시켜, 구현(adapters/llm — 현재 Ollama Cloud)을
감춘다. chat 시그니처는 기존 reporter.OllamaClient.chat 과 동일해 reporter.analyzer 함수들이 어댑터를
그대로 client 인자로 받을 수 있다(duck-type 호환).

chat_tools 는 멀티턴 도구호출(function calling)용 — 딥다이브 밸류에이션처럼 LLM 이 계산 도구를
반복 호출하고 그 결과를 보며 가정을 다듬는 에이전트 루프에 쓴다. 메시지 배열·도구 스키마를 그대로
전달하고 응답의 tool_calls 를 구조화해 돌려준다(JSON-in-text 파싱 없이).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    """LLM 요청 실패·빈 응답. 호출측은 대개 폴백(스코어만 노출 등)으로 흡수한다."""


@dataclass
class ToolCall:
    """LLM 이 요청한 도구호출 하나. arguments 는 파싱된 dict(문자열 JSON 아님)."""

    id: str
    name: str
    arguments: dict


@dataclass
class ToolTurn:
    """chat_tools 한 턴의 응답. tool_calls 가 있으면 호출측이 실행해 결과를 다음 턴에 주입한다.

    tool_calls 가 비면 최종 답변(content)으로 본다. raw_message 는 다음 턴 transcript 에 그대로
    append 할 assistant 메시지(provider 원형)."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)


class LLMPort(Protocol):
    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """system·user 프롬프트로 생성한 텍스트. 실패·빈 응답이면 LLMError."""
        ...

    def chat_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.2,
    ) -> ToolTurn:
        """메시지 배열 + 도구 스키마(OpenAI/Ollama function 형식)로 한 턴 생성.

        messages 는 {role, content, ...} dict 배열(role: system|user|assistant|tool). tools 는
        [{"type":"function","function":{"name","description","parameters"}}]. 응답의 tool_calls 를
        ToolTurn 으로 구조화해 반환. 실패·빈 응답이면 LLMError."""
        ...
