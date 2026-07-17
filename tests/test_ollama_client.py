import json
from unittest.mock import MagicMock

import pytest
import requests

from reporter.ollama_client import OllamaClient, OllamaError


def _client_with_stream(chunks: list[dict]) -> OllamaClient:
    """NDJSON 스트리밍 응답을 시뮬레이션(각 chunk 는 한 줄). iter_lines 로 흘려준다."""
    client = OllamaClient("https://ollama.com", "fake-key")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_lines.return_value = iter(json.dumps(c) for c in chunks)
    client._session = MagicMock()
    client._session.post.return_value = resp
    return client


def test_missing_api_key_raises():
    with pytest.raises(OllamaError):
        OllamaClient("https://ollama.com", "")


def test_whitespace_only_content_raises():
    client = _client_with_stream([{"message": {"content": "   \n\t "}, "done": True}])
    with pytest.raises(OllamaError):
        client.chat("glm-5.2:cloud", "sys", "user")


def test_streamed_content_is_concatenated_and_stripped():
    # 청크가 여러 개로 쪼개져 와도 이어붙여 하나의 텍스트로.
    client = _client_with_stream([
        {"message": {"content": "  분석 "}, "done": False},
        {"message": {"content": "결과"}, "done": False},
        {"message": {"content": "  "}, "done": True},
    ])
    assert client.chat("glm-5.2:cloud", "sys", "user") == "분석 결과"


def test_stops_at_done():
    # done=True 이후 청크는 무시(정상 종료).
    client = _client_with_stream([
        {"message": {"content": "끝"}, "done": True},
        {"message": {"content": "무시됨"}, "done": False},
    ])
    assert client.chat("glm-5.2:cloud", "sys", "user") == "끝"


def test_stream_error_chunk_raises():
    client = _client_with_stream([{"error": "model not found"}])
    with pytest.raises(OllamaError):
        client.chat("glm-5.2:cloud", "sys", "user")


def test_request_exception_is_wrapped_as_ollama_error():
    client = OllamaClient("https://ollama.com", "fake-key")
    client._session = MagicMock()
    client._session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(OllamaError):
        client.chat("glm-5.2:cloud", "sys", "user")


def test_http_error_is_wrapped_as_ollama_error():
    client = OllamaClient("https://ollama.com", "fake-key")
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("500")
    client._session = MagicMock()
    client._session.post.return_value = resp
    with pytest.raises(OllamaError):
        client.chat("glm-5.2:cloud", "sys", "user")


def test_request_payload_shape():
    client = _client_with_stream([{"message": {"content": "ok"}, "done": True}])
    client.chat("glm-5.2:cloud", "시스템", "유저", temperature=0.7)

    kwargs = client._session.post.call_args.kwargs
    payload = kwargs["json"]
    assert payload["model"] == "glm-5.2:cloud"
    assert payload["stream"] is True  # 스트리밍 수신
    assert kwargs["stream"] is True  # requests 스트리밍 모드
    assert payload["options"]["temperature"] == 0.7
    assert payload["messages"][0] == {"role": "system", "content": "시스템"}
    assert payload["messages"][1] == {"role": "user", "content": "유저"}


def test_chat_tools_assembles_tool_calls():
    # tool_calls 는 완결 청크의 것을 취하고 content 는 누적.
    tc = [{"function": {"name": "financials", "arguments": {"code": "093320"}}}]
    client = _client_with_stream([
        {"message": {"content": "", "tool_calls": tc}, "done": False},
        {"message": {"content": ""}, "done": True},
    ])
    msg = client.chat_tools("glm-5.2:cloud", [{"role": "user", "content": "x"}], [], temperature=0.2)
    assert msg["tool_calls"] == tc


def test_chat_tools_allows_empty_content():
    # 도구만 요청하면 content 가 비어도 정상(chat 과 달리 예외 없음).
    client = _client_with_stream([{"message": {"content": ""}, "done": True}])
    msg = client.chat_tools("glm-5.2:cloud", [{"role": "user", "content": "x"}], [])
    assert msg["content"] == ""
