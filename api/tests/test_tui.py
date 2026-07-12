"""Admin TUI 스모크 테스트 — Textual Pilot 으로 마운트·상태·프리뷰 렌더 검증.

서비스는 목킹해 실제 크롤/GLM/DB 없이 UI 로직만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app import tui
from app.services import admin_status
from app.services.server_control import ServerStatus


@dataclass
class _Preview:
    stock_name: str
    market_cap: int | None
    revenue_yoy: float | None
    momentum_3m: float | None
    coverage_count: int


def _fake_preview(db, sort="매출YoY↓", limit=50, offset=0):
    # 120건 유니버스를 흉내내 정렬·페이지 인자를 그대로 반영한다.
    total = 120
    rows = [
        _Preview(f"{sort}-{offset + i}", 100_000_000_000, 0.35, -46.0, 0)
        for i in range(min(limit, max(0, total - offset)))
    ]
    return admin_status.PreviewPage(rows=rows, total=total)


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch):
    # DB·서비스 호출을 전부 스텁으로 대체(실 자원 미사용)
    monkeypatch.setattr(tui, "init_db", lambda: None)
    monkeypatch.setattr(tui, "SessionLocal", lambda: MagicMock())
    # ServerControl/ScheduleControl 은 launchctl(macOS 전용)을 호출하므로 스텁으로 대체 —
    # 이 스모크 테스트는 UI 로직만 검증하고 OS 서비스 관리는 각 컨트롤의 단위 테스트가 맡는다.
    # (server 버튼 상호작용을 검증하는 테스트는 자체적으로 _FakeControl 을 재주입한다.)
    class _StubServerControl:
        def status(self):
            return [
                ServerStatus("api", "API", 8010, loaded=True, running=True, pid=111),
                ServerStatus("web", "WEB", 43000, loaded=True, running=True, pid=222),
            ]

        def restart(self, key):
            return f"{key} 재기동 요청됨"

        def build_web(self):
            return "WEB 빌드 완료"

    class _StubScheduleControl:
        def jobs(self):
            return []

    monkeypatch.setattr(tui, "ServerControl", _StubServerControl)
    monkeypatch.setattr(tui, "ScheduleControl", _StubScheduleControl)
    monkeypatch.setattr(
        tui.admin_status, "table_counts",
        lambda db: {"reports": 49, "universe_snapshot": 4295},
    )
    monkeypatch.setattr(
        tui.admin_status, "freshness",
        lambda db: {"latest_report_date": "2026-07-08", "latest_universe_date": "2026-07-08", "universe_today_rows": "4295"},
    )
    monkeypatch.setattr(tui.admin_status, "screener_preview", _fake_preview)
    monkeypatch.setattr(
        tui.admin_status, "db_status",
        lambda db: [admin_status.TableStatus(name="리포트", rows=49, latest="2026-07-08")],
    )
    monkeypatch.setattr(tui.admin_status, "backfill_progress", lambda db: (2, 2767))
    monkeypatch.setattr(
        tui.ingest_log, "recent",
        lambda db, limit=30: [
            tui.ingest_log.IngestLogRow(
                ts=datetime(2026, 7, 11, 2, 0), job="backfill_10y", status="ok",
                rows=200, detail="완료 200 · 실패 0 · 남음 100", duration_ms=13000,
            )
        ],
    )


async def test_tui_mounts_and_shows_status():
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Button, DataTable, Static

        status = app.query_one("#status", Static)
        status_text = str(status.render())
        assert "시스템 상태" in status_text
        assert "reports=49" in status_text

        table = app.query_one("#preview", DataTable)
        assert table.row_count == 50  # 페이지당 _PREVIEW_LIMIT

        ids = {b.id for b in app.query(Button)}
        assert {
            "ingest", "universe", "growth", "refresh", "prev", "next", "sort",
            "api_restart", "web_restart", "web_build",
        } <= ids


async def test_refresh_action_reloads():
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.action_refresh()
        await pilot.pause(0.2)
        from textual.widgets import DataTable

        assert app.query_one("#preview", DataTable).row_count == 50


async def test_pagination_next_prev():
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Button, Static

        assert app._page == 0
        assert app.query_one("#prev", Button).disabled is True  # 첫 페이지

        app.action_next_page()
        await pilot.pause(0.2)
        assert app._page == 1
        assert "51-100" in str(app.query_one("#preview_info", Static).render())
        assert app.query_one("#prev", Button).disabled is False

        app.action_prev_page()
        await pilot.pause(0.2)
        assert app._page == 0

        # 마지막 페이지(120건, 50/page → 3페이지)에서 다음 비활성
        app.action_next_page()
        app.action_next_page()
        await pilot.pause(0.2)
        assert app._page == 2
        assert app.query_one("#next", Button).disabled is True


async def test_cycle_sort_resets_page():
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Static

        app.action_next_page()
        await pilot.pause(0.2)
        assert app._page == 1

        first_sort = app._sort_keys[app._sort_idx]
        app.action_cycle_sort()
        await pilot.pause(0.2)
        # 정렬이 바뀌고 첫 페이지로 리셋
        assert app._sort_keys[app._sort_idx] != first_sort
        assert app._page == 0
        assert app._sort_keys[app._sort_idx] in str(app.query_one("#preview_info", Static).render())


async def test_running_job_disables_buttons(monkeypatch):
    # 잡 실행 중엔 트리거 버튼이 비활성화돼 이중 크롤/GLM 을 막아야 한다
    import threading

    release = threading.Event()

    def _slow_snapshot(db, snapshot_date, markets=("KOSDAQ", "KOSPI")):
        release.wait(2)  # 잡이 도는 동안 상태 검사
        return 4295

    monkeypatch.setattr(tui.universe_ingest, "snapshot_universe", _slow_snapshot)

    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from textual.widgets import Button

        await pilot.click("#universe")
        await pilot.pause(0.3)
        # 실행 중: 세 잡 버튼 모두 비활성화
        assert app._job_running is True
        assert all(app.query_one(f"#{b}", Button).disabled for b in tui.AdminTUI._JOB_BUTTONS)

        release.set()
        await pilot.pause(0.5)
        # 완료 후: 재활성화
        assert app._job_running is False
        assert not any(app.query_one(f"#{b}", Button).disabled for b in tui.AdminTUI._JOB_BUTTONS)


async def test_server_buttons_and_status(monkeypatch):
    # ServerControl 을 목킹해 실제 launchctl 없이 재기동 버튼·상태 렌더만 검증
    from app.services.server_control import ServerStatus

    restarts = []

    class _FakeControl:
        def restart(self, key):
            restarts.append(key)
            return f"{key} 재기동 요청됨"

        def build_web(self):
            return "WEB 빌드 완료"

        def status(self):
            return [
                ServerStatus("api", "API", 8010, loaded=True, running=True, pid=111),
                ServerStatus("web", "WEB", 43000, loaded=True, running=True, pid=222),
            ]

    monkeypatch.setattr(tui, "ServerControl", _FakeControl)

    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Button, Static

        # 재기동 2종 + 빌드 버튼 존재
        ids = {b.id for b in app.query(Button)}
        assert {"api_restart", "web_restart", "web_build"} <= ids

        info = app.query_one("#server_status", Static)
        assert "실행중" in str(info.render())  # 로드+실행 중

        await pilot.click("#api_restart")
        await pilot.pause(0.2)
        assert restarts == ["api"]  # 재기동을 launchctl 위임으로 호출


async def test_web_build_button_runs_build(monkeypatch):
    # WEB 빌드 버튼이 ServerControl.build_web 을 워커 스레드로 호출하는지 검증.
    from app.services.server_control import ServerStatus

    builds = []

    class _FakeControl:
        def restart(self, key):
            return "ok"

        def build_web(self):
            builds.append(True)
            return "WEB 빌드 완료"

        def status(self):
            return [ServerStatus("web", "WEB", 43000, loaded=True, running=True, pid=1)]

    monkeypatch.setattr(tui, "ServerControl", _FakeControl)

    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        await pilot.click("#web_build")
        # 워커 스레드 빌드 완료 대기
        for _ in range(20):
            await pilot.pause(0.1)
            if builds:
                break
        assert builds == [True]
