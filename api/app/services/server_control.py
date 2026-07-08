"""웹/API 서버 프로세스 제어 — TUI 에서 직접 기동·종료.

subprocess 로 직접 띄우고 그 PID 만 종료한다(다른 프로젝트·외부 서버 불간섭).
종료는 프로세스 그룹 시그널로 자식(next-server, uvicorn reload 워커 등)까지 정리한다.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

# api/app/services/server_control.py → parents[3] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_DIR = _REPO_ROOT / "api"
_WEB_DIR = _REPO_ROOT / "web"


@dataclass
class ServerSpec:
    key: str
    label: str
    port: int
    cmd: list[str]
    cwd: Path
    needs_build: Path | None = None  # 존재해야 실행 가능한 경로(web 의 .next 등)


SERVERS: dict[str, ServerSpec] = {
    "api": ServerSpec(
        key="api",
        label="API",
        port=8010,
        cmd=["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010"],
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


@dataclass
class ServerStatus:
    key: str
    label: str
    port: int
    running: bool
    pid: int | None


class ServerControl:
    """기동한 서버 프로세스를 PID 로 추적·종료한다(자신이 띄운 것만)."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}

    def start(self, key: str) -> str:
        spec = SERVERS[key]
        if self.is_running(key):
            return f"{spec.label} 이미 실행 중 (pid {self._procs[key].pid})"
        if spec.needs_build and not spec.needs_build.exists():
            return f"{spec.label} 실행 불가: 빌드 없음 ({spec.needs_build.parent} — web 은 pnpm build 필요)"
        # 새 프로세스 그룹으로 띄워 종료 시 자식까지 한 번에 정리한다.
        proc = subprocess.Popen(
            spec.cmd,
            cwd=spec.cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._procs[key] = proc
        return f"{spec.label} 기동 (pid {proc.pid}, :{spec.port})"

    def stop(self, key: str) -> str:
        spec = SERVERS[key]
        proc = self._procs.get(key)
        if not proc or proc.poll() is not None:
            self._procs.pop(key, None)
            return f"{spec.label} 실행 중 아님"
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=8)
        except (ProcessLookupError, PermissionError):
            pass
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        pid = proc.pid
        self._procs.pop(key, None)
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
