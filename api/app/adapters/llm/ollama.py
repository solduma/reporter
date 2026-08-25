"""OllamaLLMAdapter — LLMPort 를 Ollama Cloud(reporter.OllamaClient)로 구현.

reporter.ollama_client 직접 참조를 이 파일 한 곳으로 격리한다. chat 시그니처가 OllamaClient 와
동일해, reporter.analyzer 처럼 client 를 인자로 받는 기존 함수에 어댑터를 그대로 넘길 수 있다.
chat_tools 는 provider 의 message(dict)를 포트의 ToolTurn(구조화 tool_calls)으로 변환한다.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from app.ports.llm import LLMError, ToolCall, ToolTurn
from reporter.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

# 일시적 실패(타임아웃·네트워크·5xx)에 대한 재시도. 긴 딥다이브/HITL 리서치 호출이 Ollama Cloud
# 부하로 간헐 타임아웃 나던 것을 흡수한다(영구 오류는 재시도해도 같으므로 소수 회로 제한).
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 2.0  # 2s, 4s 대기(지수)
# 무료 tier rate limit(429)은 짧은 백오프로 못 버틴다 — 분 단위 대기가 필요.
_RATE_LIMIT_BACKOFF_S = 45.0  # 45s, 90s 대기


def _is_rate_limit(e: OllamaError) -> bool:
    """한도 계열 오류 판정. 클라이언트가 HTTP 상태·응답 본문을 메시지에 포함하므로 문자열 매칭으로 충분.

    ollama-cloud-proxy 의 'all ollama keys failed'(503, 전 키 소진)도 사실상 rate limit.
    """
    msg = str(e)
    return (
        "429" in msg
        or "Too Many Requests" in msg
        or "Rate limit" in msg
        or "RateLimit" in msg
        or "FreeUsageLimit" in msg
        or "all ollama keys failed" in msg
    )


# 임베딩 입력 청크 크기 / per-청크 timeout. 한 번에 수백 건을 보내면 로컬 Ollama 가 worker 등 다른
# 부하와 경합 시 180s read timeout 을 넘겨 ReadTimeout(→500) 한다. 청크로 쪼개 각 호출을 가볍게.
_EMBED_CHUNK = 64
_EMBED_TIMEOUT_S = 120


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
        out.append(
            ToolCall(
                id=str(tc.get("id") or f"call_{i}"),
                name=str(fn.get("name") or ""),
                arguments=args if isinstance(args, dict) else {},
            )
        )
    return out


class OllamaLLMAdapter:
    """LLMPort 구현. 내부 OllamaClient 를 감싸고 OllamaError 를 LLMError 로 정규화한다.

    일시적 실패(타임아웃·네트워크·부하)는 지수백오프로 재시도한다(_MAX_ATTEMPTS). 타임아웃 기본값을
    상향(300s) — 딥다이브/HITL 의 긴 리서치 프롬프트가 180s 를 넘겨 죽던 것을 완화한다.
    """

    def __init__(
        self,
        host: str,
        api_key: str,
        timeout: int = 300,
        *,
        embed_host: str = "",
    ) -> None:
        self._client = OllamaClient(host, api_key, timeout)
        # 임베딩은 cloud 가 /api/embeddings 미지원 → 로컬 Ollama 별도 엔드포인트. 빈 host 시 embed 비활성.
        self._embed_host = embed_host.rstrip("/") if embed_host else ""
        self._embed_session = requests.Session() if self._embed_host else None

    def _with_retry(self, what: str, fn, *, max_attempts: int = _MAX_ATTEMPTS):
        """fn 을 최대 max_attempts 회 시도. OllamaError 만 재시도하고, 마지막 실패는 LLMError 로 승격.

        rate limit(429)은 일시 오류 중 특수 — 짧은 지수 백오프로는 못 버티므로 분 단위 대기로
        교체한다(무료 tier 리셋 주기가 수십 초~수 분).
        """
        last: OllamaError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except OllamaError as e:
                last = e
                if attempt < max_attempts:
                    wait = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    if _is_rate_limit(e):
                        wait = max(wait, _RATE_LIMIT_BACKOFF_S * attempt)
                    # 로그 라벨은 provider 중립 — 이 어댑터는 OpenAI 호환 엔드포인트
                    # (Ollama Cloud 프록시·OpenCode Zen 등)를 두루 가리킨다.
                    logger.warning(
                        "LLM %s 실패(시도 %d/%d): %s — %.0fs 후 재시도",
                        what,
                        attempt,
                        max_attempts,
                        e,
                        wait,
                    )
                    time.sleep(wait)
        raise LLMError(str(last)) from last

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
        *,
        timeout: int | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> str:
        return self._with_retry(
            "chat",
            lambda: self._client.chat(model, system, user, temperature, timeout=timeout),
            max_attempts=max_attempts,
        )

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

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """로컬 Ollama /api/embed 로 임베딩. cloud 미지원이므로 로컬 인스턴스 사용.

        input 배열을 _EMBED_CHUNK(64) 청크로 쪼개 순차 POST — 한 번에 수백 건을 보내면 로컬 Ollama 가
        worker 등 다른 부하와 경합 시 read timeout 을 넘겨 실패한다. 청크별로 가볍게 처리해 안정.
        requests 예외(ReadTimeout·네트워크·HTTPError)·응답 형식 오류는 LLMError 로 정규화 — 호출측은
        폴백(pending 유지). model 은 호출측이 settings.ollama_embedding_model 로 전달.
        """
        if not self._embed_host:
            raise LLMError("embedding 미설정(ollama_local_host)")
        if self._embed_session is None:
            raise LLMError("embedding 세션 미초기화")
        if not texts:
            return []
        url = f"{self._embed_host}/api/embed"
        out: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_CHUNK):
            batch = texts[start : start + _EMBED_CHUNK]
            try:
                resp = self._embed_session.post(
                    url, json={"model": model, "input": batch}, timeout=_EMBED_TIMEOUT_S
                )
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                raise LLMError(f"embedding 요청 실패({len(batch)}건 청크): {e}") from e
            embs = data.get("embeddings")
            if not embs or len(embs) != len(batch):
                raise LLMError(f"embedding 응답 형식 오류: {str(data)[:80]}")
            out.extend(list(e) for e in embs)
        return out
