"""Ollama Cloud 클라이언트 — 네이티브 /api/chat 엔드포인트.

인증: Authorization: Bearer <OLLAMA_API_KEY> (https://ollama.com/settings/keys 에서 발급)
모델: glm-5.2:cloud 처럼 :cloud 접미사가 붙은 클라우드 태그.

스트리밍 수신(stream=True): 긴 생성이 read timeout 에 걸리지 않도록 NDJSON 청크를 누적한다.
timeout 은 '전체 응답 시간'이 아니라 '청크 사이 간격'에만 적용되므로(토큰이 흐르는 한 리셋),
딥다이브의 긴 tool-loop 생성도 안 끊긴다. 청크마다 message.content 를 이어붙이고 tool_calls·
최종 필드는 마지막(done) 메시지에서 조립한다.
"""

from __future__ import annotations

import json
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

    def _stream_message(self, payload: dict, what: str) -> dict:
        """stream=True 로 POST 하고 NDJSON 청크를 누적해 최종 message(dict)를 조립한다.

        각 줄은 {"message": {"content": "...", "tool_calls": [...]}, "done": false} 형태이고
        마지막 줄이 done=true. content 는 이어붙이고, tool_calls 는 등장한 청크의 것을 취한다
        (Ollama 는 tool_calls 를 쪼개지 않고 한 청크에 완결해 준다)."""
        payload = {**payload, "stream": True}
        content_parts: list[str] = []
        tool_calls: list = []
        role = "assistant"
        try:
            resp = self._session.post(self._url, json=payload, timeout=self._timeout, stream=True)
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue  # 비정형 줄은 건너뛴다(keep-alive 등)
                if chunk.get("error"):
                    raise OllamaError(f"Ollama 스트림 오류: {chunk['error']}")
                msg = chunk.get("message") or {}
                if msg.get("role"):
                    role = msg["role"]
                if msg.get("content"):
                    content_parts.append(msg["content"])
                if msg.get("tool_calls"):
                    tool_calls = msg["tool_calls"]  # 완결 tool_calls 청크
                if chunk.get("done"):
                    break
        except requests.RequestException as e:
            raise OllamaError(f"Ollama {what} 요청 실패: {e}") from e
        message: dict = {"role": role, "content": "".join(content_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature},
        }
        message = self._stream_message(payload, "요청")
        content = (message.get("content") or "").strip()
        if not content:  # 공백만 있는 응답도 빈 응답으로 간주
            raise OllamaError("Ollama 응답에 content 가 없습니다.")
        return content

    def chat_tools(
        self, model: str, messages: list[dict], tools: list[dict], temperature: float = 0.2
    ) -> dict:
        """멀티턴 도구호출. messages·tools 를 그대로 전달하고 응답 message(dict)를 반환한다.

        message 에는 content 와 (있으면) tool_calls 가 담긴다. 도구호출이 있으면 content 가 비어도
        정상이므로(모델이 도구만 요청) content 공백 검사를 하지 않는다."""
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "options": {"temperature": temperature},
        }
        return self._stream_message(payload, "tools")
