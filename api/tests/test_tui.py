"""Admin TUI 스모크 테스트 — Textual Pilot 으로 마운트·상태·프리뷰 렌더 검증.

서비스는 목킹해 실제 크롤/GLM/DB 없이 UI 로직만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app import tui
from app.services import admin_status, server_control
from app.services.server_control import ProdDeploy, ServerStatus


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
    monkeypatch.setattr(tui.admin_status, "all_backfill_progress", lambda db: [
        admin_status.BackfillStatus(domain="backfill_10y", label="일봉 10년", done=2766, total=2766, pct=100.0, remaining=0, per_run=3000),
        admin_status.BackfillStatus(domain="financials_10y", label="재무 10년", done=150, total=2766, pct=5.4, remaining=2616, per_run=150),
        admin_status.BackfillStatus(domain="report_10y", label="보고서 원문", done=2650, total=2766, pct=95.8, remaining=116, per_run=100),
        admin_status.BackfillStatus(domain="us_candle_10y", label="US 일봉 10년", done=926, total=2766, pct=33.5, remaining=1840, per_run=200),
        admin_status.BackfillStatus(domain="us_financials_10y", label="US 재무 10년", done=433, total=2766, pct=15.7, remaining=2333, per_run=60),
        admin_status.BackfillStatus(domain="related_company", label="관계사", done=2653, total=2766, pct=95.9, remaining=113, per_run=3000),
        admin_status.BackfillStatus(domain="ofs", label="OFS(별도재무)", done=7, total=2570, pct=0.3, remaining=2563, per_run=150, detail="CFS 2570개 중"),
    ])
    monkeypatch.setattr(
        tui.ingest_log, "recent",
        lambda db, limit=30: [
            tui.ingest_log.IngestLogRow(
                ts=datetime(2026, 7, 11, 2, 0), job="backfill_10y", status="ok",
                rows=200, detail="완료 200 · 실패 0 · 남음 100", duration_ms=13000,
            )
        ],
    )
    monkeypatch.setattr(tui.ingest_log, "recent_failure_count", lambda db, since_hours=24: 0)


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
        # 탭 구조: 배치(batch_*)·서버·배포·로그뷰어·프리뷰 버튼이 모두 존재.
        assert {
            "prev", "next", "sort",
            "api_restart", "web_restart", "web_build",
            "prod_preview", "prod_deploy", "cd_status",
            "log_api", "log_web", "log_worker", "log_launchd",
        } <= ids
        # scheduler.MANUAL_BATCHES 로 생성한 배치 버튼(batch_ingest_cycle 등)
        assert any(bid and bid.startswith("batch_") for bid in ids)


async def test_ingest_history_shows_no_failure_summary():
    # 실패 0건이면 적재 이력 제목에 '실패 없음' 이 뜬다.
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Static

        title = str(app.query_one("#ingest_title", Static).render())
        assert "적재 이력" in title
        assert "실패 없음" in title


async def test_ingest_history_flags_failures(monkeypatch):
    # 실패 건수가 있으면 제목에 붉은 요약('실패 N건')이 뜬다.
    monkeypatch.setattr(tui.ingest_log, "recent_failure_count", lambda db, since_hours=24: 3)
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Static

        title = str(app.query_one("#ingest_title", Static).render())
        assert "실패 3건" in title


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


async def test_running_batch_disables_buttons(monkeypatch):
    # 배치 실행 중엔 배치 버튼이 비활성화돼 이중 크롤/GLM 을 막아야 한다(_busy 상호배제).
    import threading

    release = threading.Event()
    first = tui.MANUAL_BATCHES[0]  # (key, label, fn)
    key = first[0]

    def _slow(settings=None):
        release.wait(2)
        return {"ok": True}

    # 레지스트리의 첫 배치 함수를 느린 스텁으로 교체(실 크롤 방지).
    monkeypatch.setattr(tui, "MANUAL_BATCHES", [(key, first[1], _slow)])

    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from textual.widgets import Button

        app.action_show_tab("tab_ops")  # 배치 버튼은 '운영' 탭에 있음
        await pilot.pause(0.2)
        await pilot.click(f"#batch_{key}")
        await pilot.pause(0.3)
        assert app._busy is True
        assert app.query_one(f"#batch_{key}", Button).disabled

        release.set()
        await pilot.pause(0.6)
        assert app._busy is False
        assert not app.query_one(f"#batch_{key}", Button).disabled


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

        app.action_show_tab("tab_deploy")  # 서버 버튼은 '서버/배포' 탭에 있음
        await pilot.pause(0.2)
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
        app.action_show_tab("tab_deploy")  # WEB 빌드 버튼은 '서버/배포' 탭에 있음
        await pilot.pause(0.2)
        await pilot.click("#web_build")
        # 워커 스레드 빌드 완료 대기
        for _ in range(20):
            await pilot.pause(0.1)
            if builds:
                break
        assert builds == [True]


# ── 프로덕션 배포(ProdDeploy) ────────────────────────────────────────────


def _fake_git(monkeypatch, responses):
    """server_control._git 를 (args 첫 토큰 → CompletedProcess) 매핑으로 목킹."""
    import subprocess

    calls = []

    def fake(*args, timeout=60):
        calls.append(args)
        key = args[0]
        rc, out = responses.get(key, (0, ""))
        return subprocess.CompletedProcess(args, rc, stdout=out, stderr="")

    monkeypatch.setattr(server_control, "_git", fake)
    return calls


def test_prod_deploy_pushes_when_main_ahead(monkeypatch):
    # main 이 release 보다 앞서고 ff 가능 → origin/main:release push 트리거.
    calls = _fake_git(monkeypatch, {
        "fetch": (0, ""),
        "merge-base": (0, ""),  # release 가 main 의 조상(ff 가능)
        "log": (0, "abc123 feat: x\ndef456 fix: y"),
        "push": (0, ""),
    })
    msg = ProdDeploy().deploy()
    assert "release 배포 트리거됨 (2개 커밋" in msg
    pushed = [c for c in calls if c[0] == "push"]
    assert pushed and pushed[0] == ("push", "origin", "origin/main:refs/heads/release")


def test_prod_deploy_noop_when_release_up_to_date(monkeypatch):
    # release 가 이미 main 과 동일 → push 하지 않고 안내.
    calls = _fake_git(monkeypatch, {
        "fetch": (0, ""), "merge-base": (0, ""), "log": (0, ""),
    })
    msg = ProdDeploy().deploy()
    assert "새 커밋 없음" in msg
    assert not [c for c in calls if c[0] == "push"]


def test_prod_deploy_refuses_non_fastforward(monkeypatch):
    # release 가 main 의 조상이 아님(ff 불가) → push 거부.
    _fake_git(monkeypatch, {"fetch": (0, ""), "merge-base": (1, "")})
    msg = ProdDeploy().deploy()
    assert "fast-forward 불가" in msg


def test_prod_preview_lists_pending(monkeypatch):
    _fake_git(monkeypatch, {"fetch": (0, ""), "log": (0, "abc feat: z")})
    msg = ProdDeploy().preview()
    assert "abc feat: z" in msg


# ── 탭 전환 ──────────────────────────────────────────────────────────────


async def test_tab_switching():
    # 숫자키/액션으로 탭이 전환되고, 각 탭의 대표 위젯이 마운트돼 있다.
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import TabbedContent

        tabs = app.query_one(TabbedContent)
        assert tabs.active == "tab_overview"  # 기본 개요 탭
        for tid in ("tab_ops", "tab_deploy", "tab_schedule", "tab_stocks"):
            app.action_show_tab(tid)
            await pilot.pause(0.1)
            assert tabs.active == tid


# ── 종목 검색 ────────────────────────────────────────────────────────────


async def test_stock_search_single_hit_shows_detail(monkeypatch):
    # 정확 매칭 1건이면 상세(현재가·모멘텀·재무·테마)를 렌더.
    monkeypatch.setattr(
        tui.company_service, "search_candidates",
        lambda db, q: [("005930", "삼성전자", "KOSPI", 500_000_000_000_000)],
    )
    from app.services.server_control import ServerStatus  # noqa: F401 (fixture 재사용)

    class _Snap:
        close_price = 60000
        momentum_3m = 12.5
        rs_rating = 88

    class _GM:
        revenue_yoy = 0.15

    class _Fin:
        period = "2026.03"

    monkeypatch.setattr(tui.company_service, "latest_snapshot", lambda db, code: _Snap())
    monkeypatch.setattr(tui.company_service, "growth_metric", lambda db, code: _GM())
    monkeypatch.setattr(tui.company_service, "financials_rows", lambda db, code: [_Fin()])
    monkeypatch.setattr(tui.company_service, "theme_names", lambda db, code: ["반도체", "HBM"])

    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Input, Static

        app.action_show_tab("tab_stocks")
        await pilot.pause(0.1)
        app.query_one("#search_input", Input)  # 탭에 마운트 확인
        app._run_stock_search("005930")  # 검색 워커 트리거(엔터 이벤트와 동일 경로)
        # 워커 스레드 검색 완료 대기
        for _ in range(30):
            await pilot.pause(0.1)
            if "삼성전자" in str(app.query_one("#detail", Static).render()):
                break
        detail = str(app.query_one("#detail", Static).render())
        assert "삼성전자" in detail and "88" in detail and "반도체" in detail


async def test_stock_search_multi_hit_lists_candidates(monkeypatch):
    # 다중 매칭이면 후보 목록을 보여준다(상세 아님).
    monkeypatch.setattr(
        tui.company_service, "search_candidates",
        lambda db, q: [
            ("005930", "삼성전자", "KOSPI", 5e14),
            ("005935", "삼성전자우", "KOSPI", 1e14),
        ],
    )
    app = tui.AdminTUI()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Input, Static

        app.action_show_tab("tab_stocks")
        await pilot.pause(0.1)
        app.query_one("#search_input", Input)  # 탭에 마운트 확인
        app._run_stock_search("삼성")
        for _ in range(30):
            await pilot.pause(0.1)
            if "후보" in str(app.query_one("#detail", Static).render()):
                break
        assert "후보 2건" in str(app.query_one("#detail", Static).render())


# ── CD 상태 조회 ─────────────────────────────────────────────────────────


def test_cd_status_reports_success(monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        out = '[{"status":"completed","conclusion":"success","displayTitle":"deploy x","createdAt":"","databaseId":42}]'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(server_control.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(server_control.subprocess, "run", fake_run)
    msg = ProdDeploy().cd_status()
    assert "✔ 성공" in msg and "#42" in msg


def test_cd_status_reports_in_progress(monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        out = '[{"status":"in_progress","conclusion":null,"displayTitle":"deploy y","createdAt":"","databaseId":43}]'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(server_control.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(server_control.subprocess, "run", fake_run)
    msg = ProdDeploy().cd_status()
    assert "진행중" in msg
