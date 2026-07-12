"""LLMPort — 텍스트 생성(chat) 인터페이스.

요약·분석·분류에 쓰는 LLM 호출을 이 포트에 의존시켜, 구현(adapters/llm — 현재 Ollama Cloud)을
감춘다. 시그니처는 기존 reporter.OllamaClient.chat 과 동일해 reporter.analyzer 함수들이 어댑터를
그대로 client 인자로 받을 수 있다(duck-type 호환). 모델 백엔드 교체·테스트 fake 주입이 이 포트에
어댑터를 갈아끼우는 문제로 축소된다.
"""

from __future__ import annotations

from typing import Protocol


class LLMError(RuntimeError):
    """LLM 요청 실패·빈 응답. 호출측은 대개 폴백(스코어만 노출 등)으로 흡수한다."""


class LLMPort(Protocol):
    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """system·user 프롬프트로 생성한 텍스트. 실패·빈 응답이면 LLMError."""
        ...
