"""웹/API 서버 프로세스 제어 — TUI 에서 직접 기동·종료.

subprocess 로 직접 띄우고 그 PID 만 종료한다(다른 프로젝트·외부 서버 불간섭).
종료는 프로세스 그룹 시그널로 자식(next-server, uvicorn reload 워커 등)까지 정리한다.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# api/app/services/server_control.py → parents[3] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_DIR = _REPO_ROOT / "api"
_WEB_DIR = _REPO_ROOT / "web"
_HOST = "127.0.0.1"
_LOG_DIR = _REPO_ROOT / "logs"

# 기동 직후 이 시간(초)만큼 생존을 확인한다. bind 실패 등은 이 안에 즉시 죽는다.
_STARTUP_GRACE = 1.5


@dataclass
class ServerSpec:
    key: str
    label: str
    port: int
    cmd: list[str]
    cwd: Path
    needs_build: Path | None = None  # 존재해야 실행 가능한 경로(web 의 .next 등)

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self.port}"


SERVERS: dict[str, ServerSpec] = {
    "api": ServerSpec(
        key="api",
        label="API",
        port=8010,
        cmd=["uv", "run", "uvicorn", "app.main:app", "--host", _HOST, "--port", "8010"],
        cwd=_API_DIR,
    ),
    "web": ServerSpec(
        key="web",
        label="WEB",
        port=3000,
        cmd=["pnpm", "start", "-p", "3000"],
        cwd=_WEB_DIR,
        needs_build=_WEB_DIR / ".next" / "BUILD_ID",
    ),
}


def _port_in_use(port: int) -> bool:
    """다른 프로세스가 이미 해당 포트를 리스닝 중인지 확인한다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((_HOST, port)) == 0


@dataclass
class ServerStatus:
    key: str
    label: str
    port: int
    running: bool
    pid: int | None

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self.port}"


class ServerControl:
    """기동한 서버 프로세스를 PID 로 추적·종료한다(자신이 띄운 것만)."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, object] = {}  # key → 열린 로그 파일 핸들

    @staticmethod
    def _log_tail(path: Path, lines: int = 3) -> str:
        """로그 파일 마지막 N줄(크래시 원인 힌트)."""
        with contextlib.suppress(OSError):
            tail = path.read_text(errors="replace").strip().splitlines()[-lines:]
            return "\n".join(tail)
        return ""

    def _reap(self, key: str) -> None:
        """프로세스·로그 핸들 정리(추적 목록에서 제거)."""
        self._procs.pop(key, None)
        log = self._logs.pop(key, None)
        if log is not None:
            with contextlib.suppress(Exception):
                log.close()

    def start(self, key: str) -> str:
        spec = SERVERS[key]
        if self.is_running(key):
            return f"{spec.label} 이미 실행 중 (pid {self._procs[key].pid}) — {spec.url}"
        if spec.needs_build and not spec.needs_build.exists():
            return f"{spec.label} 실행 불가: 빌드 없음 ({spec.needs_build.parent} — web 은 pnpm build 필요)"
        # 우리가 안 띄운 외부 프로세스가 포트를 점유 중이면 bind 실패로 즉시 죽으므로 미리 막는다.
        if _port_in_use(spec.port):
            return f"{spec.label} 실행 불가: 포트 {spec.port} 사용 중 (다른 서버가 이미 점유) — {spec.url}"

        # stderr 를 로그 파일로 남겨 크래시 원인(bind 실패 등)을 확인할 수 있게 한다.
        _LOG_DIR.mkdir(exist_ok=True)
        log_path = _LOG_DIR / f"server_{key}.log"
        log_file = log_path.open("w")
        # 새 프로세스 그룹으로 띄워 종료 시 자식까지 한 번에 정리한다.
        proc = subprocess.Popen(
            spec.cmd,
            cwd=spec.cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._procs[key] = proc
        self._logs[key] = log_file

        # 기동 직후 즉시 죽는지(bind 실패·설정 오류 등) 확인한다.
        time.sleep(_STARTUP_GRACE)
        if proc.poll() is not None:
            self._reap(key)
            tail = self._log_tail(log_path)
            return f"{spec.label} 기동 실패 (즉시 종료, 코드 {proc.returncode}) — {log_path}\n{tail}"
        return f"{spec.label} 기동 (pid {proc.pid}) — {spec.url}"

    def stop(self, key: str) -> str:
        spec = SERVERS[key]
        proc = self._procs.get(key)
        if not proc or proc.poll() is not None:
            self._reap(key)
            return f"{spec.label} 실행 중 아님"
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=8)
        except (ProcessLookupError, PermissionError):
            pass
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=3)  # SIGKILL 후 reap 해 좀비 방지
        pid = proc.pid
        self._reap(key)
        return f"{spec.label} 종료 (pid {pid})"

    def is_running(self, key: str) -> bool:
        proc = self._procs.get(key)
        return proc is not None and proc.poll() is None

    def status(self) -> list[ServerStatus]:
        out: list[ServerStatus] = []
        for key, spec in SERVERS.items():
            running = self.is_running(key)
            out.append(
                ServerStatus(
                    key=key,
                    label=spec.label,
                    port=spec.port,
                    running=running,
                    pid=self._procs[key].pid if running else None,
                )
            )
        return out

    def stop_all(self) -> None:
        """앱 종료 시 자신이 띄운 서버를 모두 정리한다."""
        for key in list(self._procs):
            self.stop(key)
