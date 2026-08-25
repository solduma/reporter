"""OpenAI 호환 /v1/chat/completions 클라이언트 — Ollama Cloud 프록시·OpenCode Zen 공용.

과거엔 Ollama 네이티브 /api/chat(NDJSON)을 썼지만, scripts/switch_model.sh 로 엔드포인트를
자유롭게 전환하기 위해 OpenAI 호환 와이어 포맷으로 통일했다(SSE 스트림). host 는 베이스 URL
(예: http://127.0.0.1:43187, https://opencode.ai/zen)를 받고 실제 호출은
{host}/v1/chat/completions 이다. 인증은 Authorization: Bearer <OLLAMA_API_KEY>.

스트리밍 수신(stream=True): 긴 생성이 read timeout 에 걸리지 않도록 SSE 청크를 누적한다.
timeout 은 '전체 응답 시간'이 아니라 '청크 사이 간격'에만 적용되므로(토큰이 흐르는 한 리셋),
딥다이브의 긴 tool-loop 생성도 안 끊긴다. delta.content 를 이어붙이고 tool_calls 조각(index 단위
프래그먼트)을 최종 메시지로 조립한다. 응답 message 는 OpenAI 형식(id/type/function)이라 다음 턴
transcript 에 그대로 재주입 가능하다.
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


def _error_text(err) -> str:
    """provider 오류 필드(dict|str)를 사람이 읽는 문자열로."""
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


def _harden_socket_timeout(resp: requests.Response, seconds: float) -> None:
    """응답 소켓의 recv 타임아웃을 강제한다.

    서버가 하나의 거대 HTTP 청크를 수 분에 걸쳐 트리클 하면 urllib3 의 read timeout
    (recv 단위)은 발동하지 않고, 바이트가 계속 흐르는 한 deadline 검사도 돌지 않아
    사실상 무한 대기가 된다. 소켓 자체에 간격 상한을 걸어 이런 hang 을 절단한다.
    urllib3 내부 구조 의존이 있어 실패 시 조용히 생략한다(기존 deadline 이 백업).
    """
    try:
        sock = resp.raw._fp.fp.raw._sock  # type: ignore[attr-defined]
        sock.settimeout(seconds)
    except Exception:
        pass


class OllamaClient:
    def __init__(self, host: str, api_key: str, timeout: int = 180):
        if not api_key:
            raise OllamaError("OLLAMA_API_KEY 가 설정되지 않았습니다.")
        self._url = f"{host.rstrip('/')}/v1/chat/completions"
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})
        self._timeout = timeout
        # LLM_STREAM=0 → 논스트리밍. 일부 프로바이더(proxy+minimax 등)는 거대 청크를
        # 수 분에 걸쳐 트리클 해 read timeout·deadline 검사를 모두 무력화한다.
        self._use_stream = os.getenv("LLM_STREAM", "1") != "0"

    def _non_stream_message(self, payload: dict, what: str, *, timeout: int | None = None) -> dict:
        """논스트리밍 호출 — 응답 전체를 일괄 수신.

        트리클 스트림이 read timeout·deadline 검사를 모두 무력화하는 프로바이더
        (프록시+minimax 등) 대비 모드. read timeout 이 '응답 수신 전체'의 상한으로
        동작해 병리 hang 을 절단한다.
        """
        payload = {**payload, "stream": False}
        try:
            resp = self._session.post(self._url, json=payload, timeout=timeout or self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            detail = ""
            r = getattr(e, "response", None)
            if r is not None:
                detail = (getattr(r, "text", "") or "")[:200]
            raise OllamaError(
                f"LLM {what} 요청 실패: {e}" + (f" — {detail}" if detail else "")
            ) from e
        except ValueError as e:
            raise OllamaError(f"LLM {what} JSON 파싱 실패: {e}") from e
        if data.get("error"):
            raise OllamaError(f"LLM 오류: {_error_text(data['error'])}")
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        finish = choices[0].get("finish_reason") if choices else None
        saw_reasoning = bool(msg.get("reasoning_content") or msg.get("reasoning"))
        tcs = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tcs.append(
                {
                    "id": tc.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "",
                    },
                }
            )
        message: dict = {
            "role": msg.get("role") or "assistant",
            "content": msg.get("content") or "",
        }
        if tcs:
            message["tool_calls"] = tcs
        message["_finish"] = finish
        message["_reasoning"] = saw_reasoning
        return message

    def _stream_message(self, payload: dict, what: str, *, timeout: int | None = None) -> dict:
        """stream=True 로 POST 하고 SSE 청크를 누적해 최종 assistant message(dict)를 조립한다.

        각 이벤트는 `data: {json}` 한 줄이고 종료 센티널은 `data: [DONE]`. content 는
        delta.content 를 이어붙이고, tool_calls 는 index 별 프래그먼트(id·name 은 첫 등장,
        arguments 는 문자열 누적)를 완결한다.

        timeout 은 per-call **전체 deadline**(초). stream=True 이면 requests 의 read timeout 은
        '청크 사이 간격'에만 적용되어 토큰이 느리게 흐르는(trickle) hang 은 잡지 못한다 — 따라서
        timeout 이 주어지면 (a) read timeout 을 30s 로 좁혀 청크 간 정지를 빠르게 절단하고,
        (b) 루프마다 elapsed 를 재어 timeout 전체 초과 시 OllamaError 로 끊는다(분류 호출 병리 hang 절단).
        timeout=None 이면 기존 동작(self._timeout read timeout, 전체 deadline 없음 — 딥다이브 긴 생성용).
        """
        payload = {**payload, "stream": True}
        content_parts: list[str] = []
        # index → {"id","name","args"} 프래그먼트 조립 버킷
        tc_frags: dict[int, dict] = {}
        tc_order: list[int] = []
        # 진단 정보 — 반환 message 에 잠시 붙였다가 chat/chat_tools 가 떼낸다(transcript 오염 방지).
        finish_reason: str | None = None
        saw_reasoning = False
        read_gap = 30 if timeout is not None else self._timeout
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            resp = self._session.post(self._url, json=payload, timeout=read_gap, stream=True)
            resp.raise_for_status()
            _harden_socket_timeout(resp, read_gap)
            # iter_lines 대신 iter_content 로 raw byte 를 받아 직접 줄 분할 — deadline 체크를
            # '완결 줄' 단위가 아닌 'byte 수신' 단위로 한다. 한 줄이 \n 없이 천천히 trickle 되는 hang 은
            # iter_lines 가 줄을 내놓지 않아 deadline 체크가 안 돌아 bypass 되는 병리를 막는다.
            buf = b""
            for chunk in resp.iter_content(chunk_size=None):
                if deadline is not None and time.monotonic() > deadline:
                    raise OllamaError(
                        f"LLM {what} 전체 deadline {timeout}s 초과(trickle hang 절단)"
                    )
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_s = line.decode("utf-8", "replace").strip()
                    if not line_s.startswith("data:"):
                        continue  # 빈 줄, keep-alive 주석 등
                    data = line_s[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue  # 비정형 줄은 건너뛴다
                    if obj.get("error"):
                        raise OllamaError(f"LLM 스트림 오류: {_error_text(obj['error'])}")
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    if delta.get("reasoning_content"):
                        saw_reasoning = True
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                    for frag in delta.get("tool_calls") or []:
                        raw_idx = frag.get("index")
                        idx = int(raw_idx) if raw_idx is not None else len(tc_order)
                        slot = tc_frags.get(idx)
                        if slot is None:
                            slot = tc_frags[idx] = {"id": "", "name": "", "args": ""}
                            tc_order.append(idx)
                        fn = frag.get("function") or {}
                        if frag.get("id"):
                            slot["id"] = frag["id"]
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                else:
                    continue
                break  # [DONE] 을 만나 내부 while 을 빠져나온 경우 외부 루프도 종료
        except requests.RequestException as e:
            detail = ""
            r = getattr(e, "response", None)
            if r is not None:
                detail = (getattr(r, "text", "") or "")[:200]
            raise OllamaError(
                f"LLM {what} 요청 실패: {e}" + (f" — {detail}" if detail else "")
            ) from e
        message: dict = {"role": "assistant", "content": "".join(content_parts)}
        if tc_order:
            message["tool_calls"] = [
                {
                    "id": tc_frags[i]["id"],
                    "type": "function",
                    "function": {"name": tc_frags[i]["name"], "arguments": tc_frags[i]["args"]},
                }
                for i in sorted(tc_order)
            ]
        message["_finish"] = finish_reason
        message["_reasoning"] = saw_reasoning
        return message

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
        *,
        timeout: int | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if self._use_stream:
            message = self._stream_message(payload, "요청", timeout=timeout)
        else:
            message = self._non_stream_message(payload, "요청", timeout=timeout)
        finish = message.pop("_finish", None)
        saw_reasoning = message.pop("_reasoning", False)
        content = (message.get("content") or "").strip()
        if not content:  # 공백만 있는 응답도 빈 응답으로 간주
            # reasoning 모델 진단 — 잘림과 reasoning-only 를 구분해 원인 파악 가능하게.
            if finish == "length":
                raise OllamaError(
                    "응답이 출력 토큰 한도로 잘렸습니다(reasoning 소진 추정) — 재시도 필요"
                )
            hint = ", reasoning 만 존재" if saw_reasoning else ""
            raise OllamaError(f"응답에 content 가 없습니다[finish={finish}{hint}]")
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
            "temperature": temperature,
        }
        message = (
            self._stream_message(payload, "tools")
            if self._use_stream
            else self._non_stream_message(payload, "tools", timeout=self._timeout)
        )
        # 진단 키 제거 — raw_message 는 다음 턴 transcript 에 그대로 재주입되므로 오염 금지.
        message.pop("_finish", None)
        message.pop("_reasoning", False)
        return message
