"""ResilientLLMAdapter — 주 프로바이더 rate limit 시 폴백 프로바이더로 자동 전환.

무료 tier(OpenCode Zen muse/ox, Ollama Cloud 주간 한도)는 시간·주간 창 단위로 막힌다.
한도가 걸린 프로바이더에 계속 재시도하면 인사이트 생산 자체가 정지하므로, 두 구성을
모두 받아 다음 정책으로 전환한다:

- 호출 실패가 rate limit(429 계열)이고 폴백이 있으면 **같은 요청을 폴백으로 즉시 재시도**
  하고, 이후 _FALLBACK_COOLDOWN_S 동안 폴백 우선 사용(스로틀 자연 회피).
- 쿨다운 만료 후엔 주 프로바이더로 복귀 시도(한도 리셋 감지).
- 모델명은 주↔폴백 매핑 테이블로 치환(호출측 코드 무수정).

embed 는 로컬 Ollama(공통)를 쓰므로 주 어댑터로 위임한다.
"""

from __future__ import annotations

import time

from app.ports.llm import LLMError

_FALLBACK_COOLDOWN_S = 600.0  # 10분 — 주 프로바이더 복귀 재시도 주기
_RATE_MARKERS = ("429", "Too Many Requests", "Rate limit", "RateLimit", "FreeUsageLimit")


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e)
    return any(m in msg for m in _RATE_MARKERS)


class ResilientLLMAdapter:
    """두 OllamaLLMAdapter(primary/fallback) 사이를 자동 전환하는 LLMPort 구현."""

    def __init__(
        self,
        primary,
        fallback=None,
        *,
        model_map: dict[str, str] | None = None,
        cooldown_s: float = _FALLBACK_COOLDOWN_S,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._model_map = model_map or {}
        self._cooldown_s = cooldown_s
        self._fallback_until = 0.0  # monotonic — 0 이면 항상 primary 우선

    def _pick(self):
        if time.monotonic() < self._fallback_until and self._fallback is not None:
            return self._fallback
        return self._primary

    def _map_model(self, model: str) -> str:
        if time.monotonic() < self._fallback_until:
            return self._model_map.get(model, model)
        return model

    def _invoke(self, fn_name: str, args: list, kwargs: dict):
        # chat·chat_tools 의 첫 위치 인자가 모델명 — 폴백 사용 시 매핑 테이블로 치환.
        def _remap(a):
            a = list(a)
            if fn_name in ("chat", "chat_tools") and a:
                a[0] = self._map_model(a[0])
            return a

        adapter = self._pick()
        try:
            return getattr(adapter, fn_name)(*_remap(args), **kwargs)
        except LLMError as e:
            other = self._fallback if adapter is self._primary else self._primary
            if self._fallback is not None and _is_rate_limit(e):
                self._fallback_until = time.monotonic() + self._cooldown_s
                return getattr(other, fn_name)(*_remap(args), **kwargs)
            raise

    def chat(self, model, system, user, temperature=0.3, *, timeout=None):
        return self._invoke(
            "chat", (model, system, user), {"temperature": temperature, "timeout": timeout}
        )

    def chat_tools(self, model, messages, tools, temperature=0.2):
        return self._invoke("chat_tools", (model, messages, tools), {"temperature": temperature})

    def embed(self, model, texts):
        # 임베딩은 로컬 Ollama 공용 — 프로바이더 전환과 무관하게 주 어댑터 사용.
        return self._primary.embed(model, texts)
