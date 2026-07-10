"""웹/API 서버 제어 — launchd 서비스 위임.

서버는 launchd LaunchAgent(com.reporter.server.api / .web)로 상시 등록되어
RunAtLoad+KeepAlive 로 부팅 후 자동 실행·유지된다(설치: launchd/install.sh).
여기서는 그 서비스의 상태 조회와 재기동(kickstart)만 담당한다 — 직접 프로세스를
띄우지 않으므로 TUI 세션 수명과 무관하게 서버가 유지된다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_HOST = "127.0.0.1"
_LABEL_PREFIX = "com.reporter.server"
# api/app/services/server_control.py → 프로젝트 루트(../../../..)의 web 디렉토리.
_WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def web_login_enabled() -> bool | None:
    """웹 로그인 게이트 활성 여부. web/.env.local 의 LOGIN_PASSWORD 가 비어있지 않으면 켜짐.

    비밀번호 값 자체는 절대 반환하지 않는다(설정 여부만). 파일이 없으면 None(판별 불가).
    """
    env_local = _WEB_DIR / ".env.local"
    if not env_local.is_file():
        return None
    for raw in env_local.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "LOGIN_PASSWORD":
            return bool(value.strip())
    return False  # 키가 없으면 게이트 열림(미들웨어가 PASSWORD 미설정 시 통과)


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
    "web": ServerSpec(key="web", label="WEB", port=43000),
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

    def build_web(self) -> str:
        """web 을 프로덕션 빌드한다(pnpm build). 빌드 산출물 반영엔 WEB 재기동이 필요하다."""
        pnpm = shutil.which("pnpm")
        if not pnpm:
            return "pnpm 을 찾을 수 없습니다. Node/pnpm 설치를 확인하세요."
        if not _WEB_DIR.is_dir():
            return f"web 디렉토리를 찾을 수 없습니다: {_WEB_DIR}"
        result = subprocess.run(
            [pnpm, "build"],
            cwd=_WEB_DIR,
            capture_output=True, text=True, timeout=600, check=False,
        )
        if result.returncode != 0:
            # 실패 원인은 stderr 말미가 유용하다(빌드 로그는 길어 마지막 줄들만).
            tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
            return f"WEB 빌드 실패:\n{tail}"
        return "WEB 빌드 완료 — 'WEB 재기동'을 눌러 반영하세요."
