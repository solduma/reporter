"""공용 외부 HTTP 세션 — timeout 강제·전송 오류 백오프 재시도."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from app.adapters.external import _http


def _make():
    return _http.ResilientSession()


def test_timeout_default_enforced():
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        resp = requests.Response()
        resp.status_code = 200
        return resp

    with patch.object(requests.Session, "request", fake_request):
        s = _make()
        s.get("https://x")  # timeout 미지정
    assert captured["timeout"] == _http._DEFAULT_TIMEOUT


def test_retries_on_transport_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(self, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("boom")
        resp = requests.Response()
        resp.status_code = 200
        return resp

    monkeypatch.setattr("app.adapters.external._http.time.sleep", lambda s: None)
    with patch.object(requests.Session, "request", flaky):
        resp = _make().get("https://x")
    assert resp.status_code == 200 and calls["n"] == 2


def test_raises_after_attempts_exhausted(monkeypatch):
    monkeypatch.setattr("app.adapters.external._http.time.sleep", lambda s: None)
    calls = {"n": 0}

    def always_fail(self, method, url, **kwargs):
        calls["n"] += 1
        raise requests.Timeout("t")

    with (
        patch.object(requests.Session, "request", always_fail),
        pytest.raises(requests.Timeout),
    ):
        _make().get("https://x")
    assert calls["n"] == _http._RETRY_ATTEMPTS


def test_http_4xx_not_retried():
    calls = {"n": 0}

    def http_error(self, method, url, **kwargs):
        calls["n"] += 1
        resp = requests.Response()
        resp.status_code = 404
        return resp  # raise_for_status 안 함 — 호출측 판단 계약

    with patch.object(requests.Session, "request", http_error):
        resp = _make().get("https://x")
    assert resp.status_code == 404 and calls["n"] == 1
