"""발송 스케줄 제어(schedule_control) 단위 테스트.

launchctl 호출은 목킹하고, plist 파일은 tmp_path 로 격리해 파일 이동·시각 편집을 검증한다.
"""

from __future__ import annotations

import plistlib

import pytest

from app.services import schedule_control
from app.services.schedule_control import ScheduleControl


def _write_job_plist(path, suffix: str, hour: int, minute: int, args: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": f"com.reporter.{suffix}",
        "ProgramArguments": ["/py", "-m", "reporter.cli", *args],
        "StartCalendarInterval": [
            {"Weekday": wd, "Hour": hour, "Minute": minute} for wd in range(1, 6)
        ],
    }
    with path.open("wb") as f:
        plistlib.dump(data, f)


@pytest.fixture
def agents(tmp_path, monkeypatch):
    """활성/비활성 폴더를 tmp 로 격리하고 launchctl 을 목킹한다."""
    active = tmp_path / "LaunchAgents"
    active.mkdir()
    monkeypatch.setattr(schedule_control, "_agents_dir", lambda: active)
    monkeypatch.setattr(schedule_control, "_disabled_dir", lambda: active / "reporter-disabled")
    # launchctl 호출 기록 + 항상 성공 반환
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(schedule_control.subprocess, "run", _fake_run)
    return active, calls


def test_jobs_lists_scheduled_only_and_sorted(agents):
    active, _ = agents
    _write_job_plist(active / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    _write_job_plist(active / "com.reporter.premarket.plist", "premarket", 7, 0, ["--premarket"])
    # 상시 서버는 StartCalendarInterval 이 없으므로 제외되어야 한다.
    with (active / "com.reporter.server.api.plist").open("wb") as f:
        plistlib.dump({"Label": "com.reporter.server.api", "KeepAlive": True}, f)

    ctl = ScheduleControl()
    jobs = ctl.jobs()

    suffixes = [j.suffix for j in jobs]
    assert suffixes == ["premarket", "closing"]  # 시각 순(07:00 → 17:00)
    assert all(j.enabled for j in jobs)


def test_disable_moves_plist_to_disabled_dir(agents):
    active, calls = agents
    _write_job_plist(active / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    ctl = ScheduleControl()

    msg = ctl.disable("closing")

    assert "껐습니다" in msg
    assert not (active / "com.reporter.closing.plist").exists()
    assert (active / "reporter-disabled" / "com.reporter.closing.plist").exists()
    assert any(c[:2] == ["launchctl", "bootout"] for c in calls)


def test_enable_restores_plist_and_bootstraps(agents):
    active, calls = agents
    disabled = active / "reporter-disabled"
    _write_job_plist(disabled / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    ctl = ScheduleControl()

    msg = ctl.enable("closing")

    assert "켰습니다" in msg
    assert (active / "com.reporter.closing.plist").exists()
    assert not (disabled / "com.reporter.closing.plist").exists()
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)


def test_disabled_job_appears_in_listing_as_disabled(agents):
    active, _ = agents
    disabled = active / "reporter-disabled"
    _write_job_plist(disabled / "com.reporter.afternoon.plist", "afternoon", 14, 0, ["--afternoon"])
    ctl = ScheduleControl()

    jobs = ctl.jobs()

    assert len(jobs) == 1
    assert jobs[0].suffix == "afternoon"
    assert jobs[0].enabled is False
    assert jobs[0].loaded is False


def test_set_time_updates_all_weekday_intervals(agents):
    active, calls = agents
    _write_job_plist(active / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    ctl = ScheduleControl()

    msg = ctl.set_time("closing", 18, 30)

    assert "18:30" in msg
    data = plistlib.loads((active / "com.reporter.closing.plist").read_bytes())
    intervals = data["StartCalendarInterval"]
    assert len(intervals) == 5  # 월~금 모두 갱신
    assert all(i["Hour"] == 18 and i["Minute"] == 30 for i in intervals)
    assert all(i["Weekday"] in range(1, 6) for i in intervals)
    # 활성 잡이므로 재적용(bootout→bootstrap)이 일어난다.
    assert any(c[:2] == ["launchctl", "bootout"] for c in calls)
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)


def test_set_time_rejects_invalid(agents):
    active, _ = agents
    _write_job_plist(active / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    ctl = ScheduleControl()

    assert "올바르지 않" in ctl.set_time("closing", 25, 0)


def test_set_time_on_disabled_job_edits_file_without_launchctl(agents):
    active, calls = agents
    disabled = active / "reporter-disabled"
    _write_job_plist(disabled / "com.reporter.closing.plist", "closing", 17, 0, ["--closing"])
    ctl = ScheduleControl()

    msg = ctl.set_time("closing", 16, 15)

    assert "16:15" in msg
    data = plistlib.loads((disabled / "com.reporter.closing.plist").read_bytes())
    assert all(i["Hour"] == 16 and i["Minute"] == 15 for i in data["StartCalendarInterval"])
    # 비활성 잡은 파일만 갱신하고 launchctl 을 건드리지 않는다.
    assert not any(c[:2] == ["launchctl", "bootstrap"] for c in calls)


def test_enable_missing_plist_returns_guidance(agents):
    ctl = ScheduleControl()
    msg = ctl.enable("nope")
    assert "찾을 수 없습니다" in msg
