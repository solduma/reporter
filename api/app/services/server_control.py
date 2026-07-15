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
# api/app/services/server_control.py → 프로젝트 루트(../../../..).
_PROJECT_DIR = Path(__file__).resolve().parents[3]
_WEB_DIR = _PROJECT_DIR / "web"
# 프로덕션 배포 브랜치 — release push 가 self-hosted runner 의 CD(cd.yml)를 트리거한다.
_PROD_BRANCH = "release"
_DEV_BRANCH = "main"


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


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=_PROJECT_DIR,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


class ProdDeploy:
    """프로덕션 배포 — main 의 검증된 커밋을 release 로 올려 CD(self-hosted runner)를 트리거한다.

    로컬 워킹트리를 직접 건드리는 dev 재기동(ServerControl)과 달리, prod 는 release 브랜치 push 로만
    이뤄진다(개발/배포 분리). git push 후 실제 배포는 GitHub Actions runner 가 수행한다.
    """

    def preview(self) -> str:
        """release 로 올릴 커밋을 미리 보여준다(배포 대상 파악용). 부작용 없음."""
        if _git("fetch", "origin", _DEV_BRANCH, _PROD_BRANCH).returncode != 0:
            return "git fetch 실패 — 네트워크/원격을 확인하세요."
        log = _git("log", "--oneline", f"origin/{_PROD_BRANCH}..origin/{_DEV_BRANCH}")
        pending = log.stdout.strip()
        if not pending:
            return "배포할 새 커밋 없음 — release 가 main 과 동일합니다."
        return f"release 로 올릴 커밋(main 대비):\n{pending}"

    def deploy(self) -> str:
        """origin/main 을 release 로 fast-forward 하고 push 해 CD 를 트리거한다.

        워킹트리 브랜치를 바꾸지 않도록 git push 의 로컬-refspec 기법(origin/main → release)을 쓴다.
        release 가 main 의 조상이 아니면(직접 커밋 등) 거부하고 수동 처리를 안내한다.
        """
        if _git("fetch", "origin", _DEV_BRANCH, _PROD_BRANCH).returncode != 0:
            return "git fetch 실패 — 네트워크/원격을 확인하세요."
        # release 가 main 뒤에 있는지(ff 가능) 확인 — main 이 release 를 포함해야 한다.
        anc = _git("merge-base", "--is-ancestor", f"origin/{_PROD_BRANCH}", f"origin/{_DEV_BRANCH}")
        if anc.returncode != 0:
            return (
                f"release 가 main 의 조상이 아닙니다(fast-forward 불가). "
                f"수동으로 정리하세요: git checkout {_PROD_BRANCH} && git merge {_DEV_BRANCH}"
            )
        pending = _git("log", "--oneline", f"origin/{_PROD_BRANCH}..origin/{_DEV_BRANCH}").stdout.strip()
        if not pending:
            return "배포할 새 커밋 없음 — release 가 이미 main 과 동일합니다."
        # 워킹트리 전환 없이 origin/main 을 origin/release 로 push(로컬 refspec).
        push = _git("push", "origin", f"origin/{_DEV_BRANCH}:refs/heads/{_PROD_BRANCH}", timeout=120)
        if push.returncode != 0:
            detail = (push.stderr or push.stdout).strip().splitlines()
            return "release push 실패:\n" + "\n".join(detail[-4:])
        n = len(pending.splitlines())
        return (
            f"release 배포 트리거됨 ({n}개 커밋 push) — GitHub Actions CD 가 self-hosted "
            f"runner 에서 배포합니다. 진행 상황: repo → Actions → CD."
        )

    def cd_status(self) -> str:
        """최근 CD(release 배포) run 상태를 gh CLI 로 조회한다(진행중/성공/실패 + 소요).

        gh 미설치·미인증이면 안내 문자열. TUI 가 이걸 폴링해 '배포 완료됐는지' 를 바로 보여준다.
        """
        gh = shutil.which("gh")
        if not gh:
            return "gh CLI 미설치 — CD 상태 조회 불가(brew install gh)."
        result = subprocess.run(
            [gh, "run", "list", "--workflow=cd.yml", "--limit", "1",
             "--json", "status,conclusion,displayTitle,createdAt,databaseId"],
            cwd=_PROJECT_DIR, capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return "CD 상태 조회 실패: " + (detail[-1] if detail else "gh 인증 확인")
        import json

        runs = json.loads(result.stdout or "[]")
        if not runs:
            return "CD run 없음 — 아직 release 배포 이력이 없습니다."
        r = runs[0]
        status, concl = r.get("status"), r.get("conclusion")
        title = (r.get("displayTitle") or "")[:48]
        rid = r.get("databaseId")
        if status != "completed":
            return f"CD #{rid} [진행중 ● {status}] {title}"
        mark = "✔ 성공" if concl == "success" else f"✖ {concl}"
        return f"CD #{rid} [{mark}] {title}"


def tail_service_log(key: str, lines: int = 40) -> str:
    """서비스 로그 파일의 마지막 N 줄. key: api|web|launchd. 파일 없으면 안내 문자열.

    api/web 은 launchd 가 logs/server_api.log·server_web.log 로, 배치는 launchd.log 로 남긴다.
    worker(docker)는 파일이 아니라 docker logs 이므로 여기선 다루지 않는다(별도).
    """
    log_map = {
        "api": _PROJECT_DIR / "logs" / "server_api.log",
        "web": _PROJECT_DIR / "logs" / "server_web.log",
        "launchd": _PROJECT_DIR / "logs" / "launchd.log",
    }
    path = log_map.get(key)
    if path is None:
        return f"알 수 없는 로그: {key} (api|web|launchd)"
    if not path.is_file():
        return f"로그 파일 없음: {path}"
    # 큰 파일도 안전하게 — 끝에서부터 필요한 만큼만 읽는다.
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, max(lines * 200, 4096))  # 줄당 대략치로 넉넉히
            f.seek(size - block)
            text = f.read().decode("utf-8", errors="replace")
    except OSError as e:
        return f"로그 읽기 실패: {e}"
    return "\n".join(text.splitlines()[-lines:])


def worker_log(lines: int = 40) -> str:
    """worker(docker) 컨테이너 로그 마지막 N 줄. docker 미실행·컨테이너 없음이면 안내."""
    docker = shutil.which("docker")
    if not docker:
        return "docker 미설치 — worker 로그 조회 불가."
    result = subprocess.run(
        [docker, "logs", "--tail", str(lines), "reporter-worker"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return "worker 로그 조회 실패: " + (detail[-1] if detail else "컨테이너 확인")
    # docker logs 는 stderr 로도 앱 로그를 낸다 → 둘 다 합쳐 마지막 N 줄.
    combined = (result.stdout + result.stderr).splitlines()
    return "\n".join(combined[-lines:]) or "(로그 없음)"
