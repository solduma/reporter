"""설정 → LLMPort 어댑터 선택."""

from __future__ import annotations

from app.adapters.llm.ollama import OllamaLLMAdapter
from app.config import Settings
from app.ports.llm import LLMPort


def get_llm(settings: Settings) -> LLMPort | None:
    """LLM 어댑터. OLLAMA_API_KEY 없으면 None(호출측이 LLM 기능만 비활성)."""
    if not settings.ollama_api_key:
        return None
    return OllamaLLMAdapter(settings.ollama_host, settings.ollama_api_key)
