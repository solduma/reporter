"""LLM 어댑터 — LLMPort 구현(현재 Ollama Cloud).

get_llm(settings) 로 어댑터를 얻는다(키 없으면 None). reporter.ollama_client 직접 참조는
이 패키지 안에만 있다.
"""

from app.adapters.llm.factory import get_llm
from app.adapters.llm.ollama import OllamaLLMAdapter

__all__ = ["OllamaLLMAdapter", "get_llm"]
