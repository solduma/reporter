"""발송 스케줄 제어 — launchd 예약 잡(com.reporter.<job>) 관리.

텔레그램 발송은 launchd LaunchAgent 로 평일 예약 실행된다(설치: launchd/install.sh).
여기서는 그 잡들의 조회·시각 편집·on/off 를 담당한다.

- 조회: 활성 폴더(~/Library/LaunchAgents)의 plist + `launchctl print` 로 로드 여부 판정
- 시각 편집: plist 의 StartCalendarInterval Hour/Minute 갱신 후 재적용(요일은 월~금 고정)
- on/off: bootout/bootstrap. 끄면 plist 를 비활성 폴더로 옮겨 재부팅에도 로드되지 않게 한다.

주의: 상시 서버(server.api/.web)와 데이터 수집 스케줄러(app.scheduler=APScheduler)와는 별개다.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LABEL_PREFIX = "com.reporter"
# 예약 잡만 관리한다. 상시 서버(server.*)는 server_control 이 담당하므로 제외한다.
_EXCLUDE_SUFFIXES = frozenset({"server.api", "server.web"})

# 잡 접미사 → 사람이 읽는 설명. install.sh 의 JOBS 와 대응한다.
_JOB_DESC: dict[str, str] = {
    "premarket": "미국증시 마감 + 간밤 뉴스",
    "reset": "당일 브리핑 로그 초기화",
    "perentity": "종목·산업 개별 브리핑",
    "digest_market": "시황 종합",
    "digest_invest": "투자 종합",
    "digest_econ": "경제 종합",
    "digest_bond": "채권 종합",
    "afternoon": "오후 능동 리서치",
    "closing": "마감 시황 종합",
}


def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _disabled_dir() -> Path:
    # 끈 잡의 plist 보관소. 활성 폴더에 없으면 launchd 가 로드하지 않는다(재부팅에도 유지).
    return _agents_dir() / "reporter-disabled"


def _domain() -> str:
    return f"gui/{os.getuid()}"


@dataclass
class ScheduleJob:
    suffix: str  # 라벨 접미사 (예: "closing")
    label: str  # 전체 라벨 (예: "com.reporter.closing")
    desc: str  # 사람이 읽는 설명
    hour: int
    minute: int
    enabled: bool  # 활성 폴더에 plist 가 있는지(=자동 실행 대상)
    loaded: bool  # launchctl 에 부트스트랩되어 있는지

    @property
    def time_label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def _job_suffix(label: str) -> str:
    return label[len(_LABEL_PREFIX) + 1 :]


def _read_plist(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return None


def _first_time(data: dict) -> tuple[int, int]:
    """StartCalendarInterval 첫 항목의 (hour, minute). 월~금 동일 시각을 전제한다."""
    intervals = data.get("StartCalendarInterval") or []
    first = intervals[0] if intervals else {}
    return int(first.get("Hour", 0)), int(first.get("Minute", 0))


class ScheduleControl:
    """launchd 예약 잡(com.reporter.<job>)의 조회·편집·on/off."""

    def _is_scheduled_job(self, path: Path) -> bool:
        """예약 잡 plist 인지 판별. 상시 서버는 StartCalendarInterval 이 없어 제외된다."""
        name = path.stem  # com.reporter.closing
        if not name.startswith(f"{_LABEL_PREFIX}."):
            return False
        if _job_suffix(name) in _EXCLUDE_SUFFIXES:
            return False
        data = _read_plist(path)
        return bool(data and data.get("StartCalendarInterval"))

    def _is_loaded(self, label: str) -> bool:
        result = subprocess.run(
            ["launchctl", "print", f"{_domain()}/{label}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return result.returncode == 0

    def _job_from_plist(self, path: Path, enabled: bool) -> ScheduleJob:
        data = _read_plist(path) or {}
        label = data.get("Label", path.stem)
        suffix = _job_suffix(label)
        hour, minute = _first_time(data)
        loaded = self._is_loaded(label) if enabled else False
        # news09~news16 은 시간대별 장중 뉴스라 개별 설명 대신 공통 라벨로 묶는다.
        desc = "장중 시장 뉴스" if suffix.startswith("news") else _JOB_DESC.get(suffix, suffix)
        return ScheduleJob(
            suffix=suffix,
            label=label,
            desc=desc,
            hour=hour,
            minute=minute,
            enabled=enabled,
            loaded=loaded,
        )

    def jobs(self) -> list[ScheduleJob]:
        """활성·비활성 예약 잡을 모아 시각 순으로 정렬해 반환한다."""
        found: dict[str, ScheduleJob] = {}
        for path in sorted(_agents_dir().glob(f"{_LABEL_PREFIX}.*.plist")):
            if self._is_scheduled_job(path):
                job = self._job_from_plist(path, enabled=True)
                found[job.suffix] = job
        disabled = _disabled_dir()
        if disabled.is_dir():
            for path in sorted(disabled.glob(f"{_LABEL_PREFIX}.*.plist")):
                if self._is_scheduled_job(path):
                    job = self._job_from_plist(path, enabled=False)
                    found.setdefault(job.suffix, job)
        return sorted(found.values(), key=lambda j: (j.hour, j.minute, j.suffix))

    def _plist_path(self, suffix: str) -> Path | None:
        """활성/비활성 폴더에서 잡 plist 경로를 찾는다. 없으면 None."""
        name = f"{_LABEL_PREFIX}.{suffix}.plist"
        active = _agents_dir() / name
        if active.exists():
            return active
        disabled = _disabled_dir() / name
        return disabled if disabled.exists() else None

    def _bootout(self, label: str) -> None:
        subprocess.run(
            ["launchctl", "bootout", f"{_domain()}/{label}"],
            capture_output=True, text=True, timeout=15, check=False,
        )

    def _bootstrap(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["launchctl", "bootstrap", _domain(), str(path)],
            capture_output=True, text=True, timeout=15, check=False,
        )

    def set_time(self, suffix: str, hour: int, minute: int) -> str:
        """잡의 발송 시각을 바꾼다(월~금 전체 항목 갱신). 활성 잡이면 재적용한다."""
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return f"시각이 올바르지 않습니다: {hour:02d}:{minute:02d}"
        path = self._plist_path(suffix)
        if path is None:
            return f"{suffix} plist 를 찾을 수 없습니다. './launchd/install.sh' 로 등록하세요."
        data = _read_plist(path)
        if data is None:
            return f"{suffix} plist 를 읽지 못했습니다."
        intervals = data.get("StartCalendarInterval") or []
        for item in intervals:
            item["Hour"] = hour
            item["Minute"] = minute
        with path.open("wb") as f:
            plistlib.dump(data, f)
        label = data.get("Label", f"{_LABEL_PREFIX}.{suffix}")
        # 활성(=LaunchAgents) 잡만 재적용한다. 비활성 잡은 파일만 갱신해 두면 켤 때 반영된다.
        if path.parent == _agents_dir():
            self._bootout(label)
            result = self._bootstrap(path)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                return f"{suffix} 시각 저장됨({hour:02d}:{minute:02d}) 그러나 재적용 실패: {detail}"
        return f"{suffix} 발송 시각을 {hour:02d}:{minute:02d} 로 변경했습니다."

    def disable(self, suffix: str) -> str:
        """잡을 끈다: launchctl 에서 내리고 plist 를 비활성 폴더로 옮긴다(재부팅에도 유지)."""
        name = f"{_LABEL_PREFIX}.{suffix}.plist"
        active = _agents_dir() / name
        if not active.exists():
            return f"{suffix} 은 이미 꺼져 있거나 등록되지 않았습니다."
        label = f"{_LABEL_PREFIX}.{suffix}"
        self._bootout(label)
        _disabled_dir().mkdir(parents=True, exist_ok=True)
        active.replace(_disabled_dir() / name)
        return f"{suffix} 발송을 껐습니다."

    def enable(self, suffix: str) -> str:
        """잡을 켠다: plist 를 활성 폴더로 되돌리고 bootstrap 한다."""
        name = f"{_LABEL_PREFIX}.{suffix}.plist"
        disabled = _disabled_dir() / name
        active = _agents_dir() / name
        if active.exists():
            return f"{suffix} 은 이미 켜져 있습니다."
        if not disabled.exists():
            return f"{suffix} plist 를 찾을 수 없습니다. './launchd/install.sh' 로 등록하세요."
        disabled.replace(active)
        result = self._bootstrap(active)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"{suffix} 켜기 실패: {detail}"
        return f"{suffix} 발송을 켰습니다."

    def toggle(self, suffix: str, enabled: bool) -> str:
        """현재 활성 상태(enabled)의 반대로 전환한다."""
        return self.disable(suffix) if enabled else self.enable(suffix)
