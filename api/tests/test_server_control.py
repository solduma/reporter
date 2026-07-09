"""서버 제어 단위 테스트 — launchctl 위임(status/restart)을 목킹해 검증.

실제 launchctl 을 부르지 않도록 subprocess.run 을 대체한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services import server_control
from app.services.server_control import ServerControl

# launchctl print 출력 예시(로드+실행 중). pid 라인이 있으면 running.
_PRINT_RUNNING = """\
com.reporter.server.api = {
	active count = 1
	state = running
	pid = 4242
	program = /usr/bin/uv
}
"""
_PRINT_LOADED_NOT_RUNNING = """\
com.reporter.server.api = {
	state = waiting
}
"""


@dataclass
class _Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@pytest.fixture
def fake_launchctl(monkeypatch):
    """launchctl 호출을 가로채 (argv, 반환) 을 제어한다."""
    calls = []
    responses = {"print": _Result(1), "kickstart": _Result(0)}  # 기본: 미등록

    def _run(argv, **kwargs):
        calls.append(argv)
        sub = argv[1]  # launchctl <sub> ...
        return responses.get(sub, _Result(0))

    monkeypatch.setattr(server_control.subprocess, "run", _run)
    return calls, responses


def test_status_not_loaded(fake_launchctl):
    _calls, responses = fake_launchctl
    responses["print"] = _Result(1)  # 미등록
    st = {s.key: s for s in ServerControl().status()}
    assert st["api"].loaded is False
    assert st["api"].running is False
    assert st["api"].pid is None
    assert st["api"].url == "http://127.0.0.1:8010"


def test_status_loaded_and_running(fake_launchctl):
    _calls, responses = fake_launchctl
    responses["print"] = _Result(0, stdout=_PRINT_RUNNING)
    st = {s.key: s for s in ServerControl().status()}
    assert st["api"].loaded is True
    assert st["api"].running is True
    assert st["api"].pid == 4242


def test_status_loaded_not_running(fake_launchctl):
    _calls, responses = fake_launchctl
    responses["print"] = _Result(0, stdout=_PRINT_LOADED_NOT_RUNNING)
    st = {s.key: s for s in ServerControl().status()}
    assert st["api"].loaded is True
    assert st["api"].running is False  # pid 없음 → 대기(재시작 중)
    assert st["api"].pid is None


def test_restart_kicks_when_loaded(fake_launchctl):
    calls, responses = fake_launchctl
    responses["print"] = _Result(0, stdout=_PRINT_RUNNING)
    responses["kickstart"] = _Result(0)
    msg = ServerControl().restart("api")
    assert "재기동 요청됨" in msg
    # kickstart -k <domain>/<label> 이 호출됐는지
    kick = [c for c in calls if c[1] == "kickstart"]
    assert kick and "-k" in kick[0]
    assert any("com.reporter.server.api" in a for a in kick[0])


def test_restart_when_not_loaded_guides_install(fake_launchctl):
    _calls, responses = fake_launchctl
    responses["print"] = _Result(1)  # 미등록
    msg = ServerControl().restart("api")
    assert "미등록" in msg and "install.sh" in msg


def test_restart_reports_kickstart_failure(fake_launchctl):
    _calls, responses = fake_launchctl
    responses["print"] = _Result(0, stdout=_PRINT_RUNNING)
    responses["kickstart"] = _Result(1, stderr="Could not find service")
    msg = ServerControl().restart("api")
    assert "재기동 실패" in msg
