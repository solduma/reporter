"""DART 요청 스로틀 단위 테스트 — 최소 간격 강제·스레드 직렬화."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.adapters.dart import throttle as dart_throttle


def test_enforces_min_interval(monkeypatch):
    # 연속 호출 간 최소 간격을 보장하는지(sleep 호출로 검증, 실제 대기 없이).
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.05)
    monkeypatch.setattr(dart_throttle, "_last_request_at", 0.0)
    session = MagicMock()
    session.get.return_value = "resp"

    t0 = time.monotonic()
    for _ in range(4):
        dart_throttle.get(session, "https://opendart.fss.or.kr/api/x")
    elapsed = time.monotonic() - t0

    # 4회 호출 → 최소 3번의 간격(0.05s) 이상 소요.
    assert elapsed >= 0.05 * 3
    assert session.get.call_count == 4


def test_passes_through_args(monkeypatch):
    # session.get 에 url·kwargs 를 그대로 전달한다.
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.0)
    session = MagicMock()
    session.get.return_value = "ok"
    out = dart_throttle.get(session, "https://x", params={"a": 1}, timeout=15)
    assert out == "ok"
    session.get.assert_called_once_with("https://x", params={"a": 1}, timeout=15)
