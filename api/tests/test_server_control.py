"""서버 프로세스 제어 단위 테스트 — subprocess 를 목킹해 기동·종료·상태 검증."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services import server_control
from app.services.server_control import SERVERS, ServerControl


class _FakeProc:
    def __init__(self, pid=1234):
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False


@pytest.fixture
def _patch(monkeypatch):
    procs = []

    def _popen(cmd, **kwargs):
        p = _FakeProc(pid=1000 + len(procs))
        procs.append(p)
        return p

    monkeypatch.setattr(server_control.subprocess, "Popen", _popen)
    monkeypatch.setattr(server_control.os, "killpg", lambda pgid, sig: None)
    monkeypatch.setattr(server_control.os, "getpgid", lambda pid: pid)
    # web needs_build 검사를 통과시킴
    monkeypatch.setattr(SERVERS["web"], "needs_build", None)
    return procs


def test_start_and_status(_patch):
    sc = ServerControl()
    msg = sc.start("api")
    assert "기동" in msg and "8010" in msg
    st = {s.key: s for s in sc.status()}
    assert st["api"].running is True and st["api"].pid is not None
    assert st["web"].running is False


def test_double_start_is_noop(_patch):
    sc = ServerControl()
    sc.start("api")
    msg = sc.start("api")
    assert "이미 실행 중" in msg
    assert len(_patch) == 1  # Popen 은 한 번만


def test_stop(_patch):
    sc = ServerControl()
    sc.start("api")
    msg = sc.stop("api")
    assert "종료" in msg
    assert sc.is_running("api") is False


def test_stop_when_not_running(_patch):
    sc = ServerControl()
    assert "실행 중 아님" in sc.stop("web")


def test_stop_all(_patch):
    sc = ServerControl()
    sc.start("api")
    sc.start("web")
    sc.stop_all()
    assert not sc.is_running("api")
    assert not sc.is_running("web")


def test_web_requires_build(monkeypatch):
    # 빌드 산출물이 없으면 web 은 실행 거부
    monkeypatch.setattr(server_control.subprocess, "Popen", lambda *a, **k: _FakeProc())
    fake_missing = MagicMock()
    fake_missing.exists.return_value = False
    monkeypatch.setattr(SERVERS["web"], "needs_build", fake_missing)
    sc = ServerControl()
    msg = sc.start("web")
    assert "빌드 없음" in msg
    assert sc.is_running("web") is False
