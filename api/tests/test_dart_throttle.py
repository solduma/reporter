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
    dart_throttle.configure_keys()  # 링 비움 → 단순 스로틀 경로
    session = MagicMock()
    session.get.return_value = "ok"
    out = dart_throttle.get(session, "https://x", params={"a": 1}, timeout=15)
    assert out == "ok"
    session.get.assert_called_once_with("https://x", params={"a": 1}, timeout=15)


# ── 키 폴오버(020 한도초과) ────────────────────────────────────────────
def _resp(content: bytes) -> MagicMock:
    r = MagicMock()
    r.content = content
    return r


_QUOTA_JSON = b'{"status":"020","message":"\xec\x82\xac\xec\x9a\xa9\xed\x95\x9c\xeb\x8f\x84"}'
_QUOTA_XML = b'<?xml version="1.0"?><result><status>020</status></result>'
_OK_JSON = b'{"status":"000","list":[]}'


def test_failover_rotates_to_backup_on_quota(monkeypatch):
    # primary 가 020 을 주면 backup 키로 회전해 재시도하고, 성공 응답을 반환한다.
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(dart_throttle, "_last_request_at", 0.0)
    dart_throttle.configure_keys("primary", "backup")
    session = MagicMock()
    session.get.side_effect = [_resp(_QUOTA_JSON), _resp(_OK_JSON)]

    params = {"crtfc_key": "primary", "corp_code": "x"}
    out = dart_throttle.get(session, "https://x", params=params)

    assert out.content == _OK_JSON
    assert session.get.call_count == 2
    # 두 번째 호출은 backup 키로 나갔다(params 를 활성 키로 덮어씀).
    assert params["crtfc_key"] == "backup"
    assert dart_throttle.active_key() == "backup"


def test_failover_detects_quota_in_xml_body(monkeypatch):
    # document.xml(바이너리 엔드포인트)도 020 시 XML 본문을 주므로 XML 도 감지해 회전.
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(dart_throttle, "_last_request_at", 0.0)
    dart_throttle.configure_keys("primary", "backup")
    session = MagicMock()
    session.get.side_effect = [_resp(_QUOTA_XML), _resp(b"PK\x03\x04zipdata")]

    params = {"crtfc_key": "primary"}
    out = dart_throttle.get(session, "https://doc", params=params)

    assert out.content.startswith(b"PK")
    assert dart_throttle.active_key() == "backup"


def test_all_keys_exhausted_returns_last_quota_response(monkeypatch):
    # 모든 키가 020 이면 마지막 020 응답을 그대로 반환(client 가 DartQuotaExceeded 로 올림).
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(dart_throttle, "_last_request_at", 0.0)
    dart_throttle.configure_keys("primary", "backup")
    session = MagicMock()
    session.get.side_effect = [_resp(_QUOTA_JSON), _resp(_QUOTA_JSON)]

    out = dart_throttle.get(session, "https://x", params={"crtfc_key": "primary"})

    assert b"020" in out.content
    assert session.get.call_count == 2  # primary·backup 각 1회, 무한재시도 없음
    assert dart_throttle.active_key() is None  # 링 소진


def test_success_keeps_active_key_no_rewaste(monkeypatch):
    # 한 번 backup 으로 넘어가면 이후 성공 호출은 backup 을 유지(primary 재시도로 020 낭비 안 함).
    monkeypatch.setattr(dart_throttle, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(dart_throttle, "_last_request_at", 0.0)
    dart_throttle.configure_keys("primary", "backup")
    session = MagicMock()
    session.get.side_effect = [_resp(_QUOTA_JSON), _resp(_OK_JSON), _resp(_OK_JSON)]

    dart_throttle.get(session, "https://x", params={"crtfc_key": "primary"})  # 회전
    p2 = {"crtfc_key": "primary"}
    dart_throttle.get(session, "https://x", params=p2)  # 두 번째 성공 호출

    assert p2["crtfc_key"] == "backup"  # 활성 키 유지, primary 재시도 없음
    assert session.get.call_count == 3
