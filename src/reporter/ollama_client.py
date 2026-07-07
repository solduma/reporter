"""Ollama Cloud 클라이언트 — 네이티브 /api/chat 엔드포인트.

인증: Authorization: Bearer <OLLAMA_API_KEY> (https://ollama.com/settings/keys 에서 발급)
모델: glm-5.2:cloud 처럼 :cloud 접미사가 붙은 클라우드 태그.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str, api_key: str, timeout: int = 180):
        if not api_key:
            raise OllamaError("OLLAMA_API_KEY 가 설정되지 않았습니다.")
        self._url = f"{host.rstrip('/')}/api/chat"
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})
        self._timeout = timeout

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            resp = self._session.post(self._url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise OllamaError(f"Ollama 요청 실패: {e}") from e

        data = resp.json()
        content = (data.get("message", {}).get("content") or "").strip()
        if not content:  # 공백만 있는 응답도 빈 응답으로 간주
            raise OllamaError(f"Ollama 응답에 content 가 없습니다: {data}")
        return content
