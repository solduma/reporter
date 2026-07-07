from unittest.mock import MagicMock

import pytest

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
