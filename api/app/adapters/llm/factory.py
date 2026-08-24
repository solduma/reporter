"""설정 → LLMPort 어댑터 선택."""

from __future__ import annotations

from app.adapters.llm.ollama import OllamaLLMAdapter
from app.config import Settings
from app.ports.llm import LLMPort


def get_llm(settings: Settings) -> LLMPort | None:
    """LLM 어댑터. OLLAMA_API_KEY 없으면 None(호출측이 LLM 기능만 비활성).

    FALLBACK_* 설정이 있으면 ResilientLLMAdapter 로 감싸 rate limit 시 자동 전환.
    """
    if not settings.ollama_api_key:
        return None
    primary = OllamaLLMAdapter(
        settings.ollama_host,
        settings.ollama_api_key,
        embed_host=settings.ollama_local_host,
    )
    if not (settings.fallback_ollama_host and settings.fallback_ollama_api_key):
        return primary

    from app.adapters.llm.resilient import ResilientLLMAdapter

    fallback = OllamaLLMAdapter(
        settings.fallback_ollama_host,
        settings.fallback_ollama_api_key,
        embed_host=settings.ollama_local_host,
    )
    model_map = {}
    if settings.fallback_summary_model:
        model_map[settings.summary_model] = settings.fallback_summary_model
    if settings.fallback_insight_model:
        model_map[settings.insight_model] = settings.fallback_insight_model
    return ResilientLLMAdapter(primary, fallback, model_map=model_map)
