"""Admin TUI 스모크 테스트 — Textual Pilot 으로 마운트·상태·프리뷰 렌더 검증.

서비스는 목킹해 실제 크롤/GLM/DB 없이 UI 로직만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app import tui
from app.services import admin_status


@dataclass
class _Preview:
    stock_name: str
    market_cap: int | None
    revenue_yoy: float | None
    momentum_3m: float | None
    coverage_count: int


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch):
    # DB·서비스 호출을 전부 스텁으로 대체(실 자원 미사용)
    monkeypatch.setattr(tui, "init_db", lambda: None)
    monkeypatch.setattr(tui, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(admin_status, "table_counts", lambda db: {"reports": 49, "universe_snapshot": 4295})
    monkeypatch.setattr(
        admin_status, "freshness",
        lambda db: {"latest_report_date": "2026-07-08", "latest_universe_date": "2026-07-08", "universe_today_rows": "4295"},
    )
    monkeypatch.setattr(tui.admin_status, "table_counts", admin_status.table_counts)
    monkeypatch.setattr(tui.admin_status, "freshness", admin_status.freshness)
    monkeypatch.setattr(
        tui.admin_status, "screener_preview",
        lambda db, limit=10: [_Preview("노타", 384900000000, 35.0, -46.0, 0)],
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
        assert table.row_count == 1

        assert {b.id for b in app.query(Button)} == {"ingest", "universe", "growth", "refresh"}


async def test_refresh_action_reloads():
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.action_refresh()
        await pilot.pause(0.2)
        from textual.widgets import DataTable

        assert app.query_one("#preview", DataTable).row_count == 1


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
