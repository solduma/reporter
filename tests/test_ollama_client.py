from unittest.mock import MagicMock

import pytest
import requests

from reporter.ollama_client import OllamaClient, OllamaError


def _client_with_response(json_body: dict) -> OllamaClient:
    client = OllamaClient("https://ollama.com", "fake-key")
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    client._session = MagicMock()
    client._session.post.return_value = resp
    return client


def test_missing_api_key_raises():
    with pytest.raises(OllamaError):
        OllamaClient("https://ollama.com", "")


def test_whitespace_only_content_raises():
    client = _client_with_response({"message": {"content": "   \n\t "}})
    with pytest.raises(OllamaError):
        client.chat("glm-5.2:cloud", "sys", "user")


def test_valid_content_is_returned_stripped():
    client = _client_with_response({"message": {"content": "  분석 결과  "}})
    assert client.chat("glm-5.2:cloud", "sys", "user") == "분석 결과"


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
    client = _client_with_response({"message": {"content": "ok"}})
    client.chat("glm-5.2:cloud", "시스템", "유저", temperature=0.7)

    payload = client._session.post.call_args.kwargs["json"]
    assert payload["model"] == "glm-5.2:cloud"
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.7
    assert payload["messages"][0] == {"role": "system", "content": "시스템"}
    assert payload["messages"][1] == {"role": "user", "content": "유저"}
