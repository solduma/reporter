import json
from unittest.mock import MagicMock

import pytest
import requests

from reporter.ollama_client import OllamaClient, OllamaError


def _sse_bytes(events: list[dict | str]) -> bytes:
    """SSE 프레임 직렬화. str 항목은 그대로(예: '[DONE]'), dict 는 data: {json} 한 줄."""
    out = b""
    for ev in events:
        body = ev if isinstance(ev, str) else json.dumps(ev)
        out += f"data: {body}\n\n".encode()
    return out


def _client_with_stream(payload: bytes) -> OllamaClient:
    """SSE 스트리밍 응답을 시뮬레이션. iter_content 로 byte 를 흘려준다."""
    client = OllamaClient("https://opencode.ai/zen", "fake-key")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_content.return_value = iter([payload])
    client._session = MagicMock()
    client._session.post.return_value = resp
    return client


def test_url_is_openai_compat():
    assert (
        OllamaClient("http://127.0.0.1:43187/", "k")._url
        == "http://127.0.0.1:43187/v1/chat/completions"
    )


def test_missing_api_key_raises():
    with pytest.raises(OllamaError):
        OllamaClient("https://opencode.ai/zen", "")


def test_whitespace_only_content_raises():
    client = _client_with_stream(
        _sse_bytes(
            [
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "   \n\t "}, "finish_reason": None}
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ]
        )
    )
    with pytest.raises(OllamaError):
        client.chat("x-preview-f-free", "sys", "user")


def test_streamed_content_is_concatenated_and_stripped():
    # 청크가 여러 개로 쪼개져 와도 이어붙여 하나의 텍스트로.
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"content": "  분석 "}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "결과"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ]
        )
    )
    assert client.chat("x-preview-f-free", "sys", "user") == "분석 결과"


def test_stops_at_done_sentinel():
    # [DONE] 이후 이벤트는 무시(정상 종료). 뒤에 오류 이벤트가 와도 무시돼야 한다.
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"content": "끝"}, "finish_reason": None}]},
                "[DONE]",
                {"error": "무시됨"},
            ]
        )
    )
    assert client.chat("x-preview-f-free", "sys", "user") == "끝"


def test_stream_error_chunk_raises():
    client = _client_with_stream(
        _sse_bytes(
            [
                {"error": {"message": "weekly usage limit exceeded"}},
            ]
        )
    )
    with pytest.raises(OllamaError, match="usage limit"):
        client.chat("qwen3.5:cloud", "sys", "user")


def test_request_exception_is_wrapped_as_ollama_error():
    client = OllamaClient("https://opencode.ai/zen", "fake-key")
    client._session = MagicMock()
    client._session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(OllamaError):
        client.chat("x-preview-f-free", "sys", "user")


def test_http_error_body_is_included_in_error():
    # 4xx/5xx 본문(예: quota 오류 JSON)이 OllamaError 메시지에 들어가 진단 가능해야 한다.
    client = OllamaClient("https://opencode.ai/zen", "fake-key")
    err = requests.HTTPError("400 Client Error")
    err.response = MagicMock(text='{"error":{"message":"invalid api key"}}')
    resp = MagicMock()
    resp.raise_for_status.side_effect = err
    client._session = MagicMock()
    client._session.post.return_value = resp
    with pytest.raises(OllamaError, match="invalid api key"):
        client.chat("x-preview-f-free", "sys", "user")


def test_request_payload_shape():
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]},
                "[DONE]",
            ]
        )
    )
    client.chat("glm-5.2:cloud", "시스템", "유저", temperature=0.7)

    kwargs = client._session.post.call_args.kwargs
    payload = kwargs["json"]
    assert client._session.post.call_args.args[0] == "https://opencode.ai/zen/v1/chat/completions"
    assert payload["model"] == "glm-5.2:cloud"
    assert payload["stream"] is True  # 스트리밍 수신
    assert kwargs["stream"] is True  # requests 스트리밍 모드
    assert payload["temperature"] == 0.7
    assert payload["messages"][0] == {"role": "system", "content": "시스템"}
    assert payload["messages"][1] == {"role": "user", "content": "유저"}


def test_chat_tools_assembles_fragmented_tool_calls():
    # OpenAI 스트림은 tool_calls 를 index 별 프래그먼트로 쪼개 보낸다 — id·name 은 첫 등장,
    # arguments 는 문자열 누적.
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "financials", "arguments": ""},
                                    },
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '{"code": "093320"}'}},
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                "[DONE]",
            ]
        )
    )
    msg = client.chat_tools(
        "glm-5.2:cloud", [{"role": "user", "content": "x"}], [], temperature=0.2
    )
    assert msg["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "financials", "arguments": '{"code": "093320"}'},
        }
    ]


def test_chat_tools_multiple_calls_keep_order_and_content():
    client = _client_with_stream(
        _sse_bytes(
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "a",
                                        "type": "function",
                                        "function": {"name": "f1", "arguments": "{}"},
                                    },
                                    {
                                        "index": 1,
                                        "id": "b",
                                        "type": "function",
                                        "function": {"name": "f2", "arguments": "{}"},
                                    },
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                "[DONE]",
            ]
        )
    )
    msg = client.chat_tools("glm-5.2:cloud", [{"role": "user", "content": "x"}], [])
    assert [tc["id"] for tc in msg["tool_calls"]] == ["a", "b"]
    assert msg["content"] == ""


def test_chat_tools_allows_empty_content():
    # 도구만 요청하면 content 가 비어도 정상(chat 과 달리 예외 없음).
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                "[DONE]",
            ]
        )
    )
    msg = client.chat_tools("glm-5.2:cloud", [{"role": "user", "content": "x"}], [])
    assert msg["content"] == ""


def test_chat_total_deadline_truncates_trickle(monkeypatch):
    # trickle hang: 청크는 오지만 전체 elapsed 가 deadline 초과 → OllamaError 로 절단.
    # read timeout 은 청크 간격만 보므로 trickle 을 못 잡는 것을 전체 deadline 보완.
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"content": "a"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "b"}, "finish_reason": "stop"}]},
                "[DONE]",
            ]
        )
    )
    calls = {"n": 0}

    def fake_monotonic() -> float:
        v = 0.0 if calls["n"] == 0 else 100.0  # deadline 계산=0(→90), 루프=100(>90) → 즉시 절단
        calls["n"] += 1
        return v

    monkeypatch.setattr("reporter.ollama_client.time.monotonic", fake_monotonic)
    with pytest.raises(OllamaError):
        client.chat("x-preview-f-free", "sys", "user", timeout=90)


def test_chat_within_deadline_succeeds(monkeypatch):
    # deadline 내 정상 스트리밍 응답은 그대로 완료.
    client = _client_with_stream(
        _sse_bytes(
            [
                {"choices": [{"index": 0, "delta": {"content": "분석 "}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "결과"}, "finish_reason": "stop"}]},
                "[DONE]",
            ]
        )
    )
    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        return float(calls["n"])  # 1,2,3... — deadline=1+90=91 보다 작아 계속 진행

    monkeypatch.setattr("reporter.ollama_client.time.monotonic", fake_monotonic)
    assert client.chat("x-preview-f-free", "sys", "user", timeout=90) == "분석 결과"


def test_chat_deadline_fires_mid_line_on_trickle(monkeypatch):
    # trickle hang 의 핵심 병리: \n 없이 byte 가 천천히 흐르는 한 줄. iter_lines 은 줄이 완결되지
    # 않아 yield 하지 않으므로 per-line deadline 체크가 안 돌아 bypass 된다. iter_content 전환으로
    # byte 수신마다 deadline 을 검사 → partial line 도 두 번째 byte 수신에서 절단된다.
    client = OllamaClient("https://opencode.ai/zen", "fake-key")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    # \n 없는 byte 청크 — data: 줄이 완결되지 않은 채 흘러옴.
    resp.iter_content.return_value = iter(
        [b'data: {"choices":[{"delta":{"content":"a"', b'"finish_reason":null}]}']
    )
    client._session = MagicMock()
    client._session.post.return_value = resp
    calls = {"n": 0}

    def fake_monotonic() -> float:
        v = 0.0 if calls["n"] == 0 else 100.0  # 첫 byte=0(허용), 둘째 byte=100(>90 → 절단)
        calls["n"] += 1
        return v

    monkeypatch.setattr("reporter.ollama_client.time.monotonic", fake_monotonic)
    with pytest.raises(OllamaError):
        client.chat("x-preview-f-free", "sys", "user", timeout=90)
