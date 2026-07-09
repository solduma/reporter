"""웹/API 서버 제어 — launchd 서비스 위임.

서버는 launchd LaunchAgent(com.reporter.server.api / .web)로 상시 등록되어
RunAtLoad+KeepAlive 로 부팅 후 자동 실행·유지된다(설치: launchd/install.sh).
여기서는 그 서비스의 상태 조회와 재기동(kickstart)만 담당한다 — 직접 프로세스를
띄우지 않으므로 TUI 세션 수명과 무관하게 서버가 유지된다.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

_HOST = "127.0.0.1"
_LABEL_PREFIX = "com.reporter.server"


@dataclass
class ServerSpec:
    key: str
    label: str
    port: int

    @property
    def service_label(self) -> str:
        return f"{_LABEL_PREFIX}.{self.key}"

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self.port}"


SERVERS: dict[str, ServerSpec] = {
    "api": ServerSpec(key="api", label="API", port=8010),
    "web": ServerSpec(key="web", label="WEB", port=3000),
}


@dataclass
class ServerStatus:
    key: str
    label: str
    port: int
    loaded: bool  # launchd 에 등록(부트스트랩)되어 있는지
    running: bool  # 현재 프로세스가 떠 있는지(PID 존재)
    pid: int | None

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self.port}"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _service_target(label: str) -> str:
    return f"{_domain()}/{label}"


# launchctl print 출력에서 pid/state 를 뽑는 정규식.
_PID_RE = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)


class ServerControl:
    """launchd 서비스(com.reporter.server.*)의 상태 조회·재기동."""

    def _print(self, label: str) -> str | None:
        """launchctl print 출력. 서비스가 미등록이면 None."""
        result = subprocess.run(
            ["launchctl", "print", _service_target(label)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return None  # 미등록(로드 안 됨)
        return result.stdout

    def _status_of(self, spec: ServerSpec) -> ServerStatus:
        out = self._print(spec.service_label)
        if out is None:
            return ServerStatus(spec.key, spec.label, spec.port, loaded=False, running=False, pid=None)
        m = _PID_RE.search(out)
        pid = int(m.group(1)) if m else None
        return ServerStatus(
            spec.key, spec.label, spec.port, loaded=True, running=pid is not None, pid=pid
        )

    def status(self) -> list[ServerStatus]:
        return [self._status_of(spec) for spec in SERVERS.values()]

    def is_loaded(self, key: str) -> bool:
        return self._print(SERVERS[key].service_label) is not None

    def restart(self, key: str) -> str:
        """서비스를 재기동한다(launchctl kickstart -k). 미등록이면 안내를 반환."""
        spec = SERVERS[key]
        if not self.is_loaded(key):
            return (
                f"{spec.label} 미등록 — launchd 서비스가 없습니다. "
                f"'./launchd/install.sh' 로 서버 서비스를 등록하세요."
            )
        # -k: 실행 중이면 죽이고 다시 시작. KeepAlive 서비스라 즉시 재기동된다.
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", _service_target(spec.service_label)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"{spec.label} 재기동 실패: {detail} — {spec.url}"
        return f"{spec.label} 재기동 요청됨 — {spec.url}"
