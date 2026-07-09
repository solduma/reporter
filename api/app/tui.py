"""Admin TUI (Textual) — 수집 트리거 + 시스템 상태 모니터링.

서비스 계층을 직접 호출한다(HTTP 미경유). 무거운 작업은 워커 스레드에서 돌려
UI 를 막지 않고, 진행 상황을 로그 패널에 스트리밍한다.

실행: cd api && uv run reporter-tui
주의: 트리거는 실제 크롤/GLM/네이버 호출을 수행한다(라이브 자원 사용).
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Log, Static

from app.config import get_settings
from app.db.session import SessionLocal, init_db
from app.services import admin_status, broadcast_ingest, growth_ingest, ingest, universe_ingest
from app.services.server_control import ServerControl


class _LogHandler(logging.Handler):
    """서비스 로거 → TUI Log 위젯으로 흘려보낸다.

    서비스는 워커 스레드에서 로그를 남기므로, 위젯 갱신은 반드시 이벤트 루프로
    마셜링한다(call_from_thread). 직접 write_line 은 스레드 안전하지 않다.
    """

    def __init__(self, app: App, log_widget: Log):
        super().__init__(level=logging.INFO)
        self._app = app
        self._log = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # 위젯 파괴·루프 종료 등은 무시
            self._app.call_from_thread(self._log.write_line, self.format(record))


class AdminTUI(App):
    TITLE = "reporter admin"
    CSS = """
    #status { height: auto; border: round $accent; padding: 1; }
    #actions { height: auto; padding: 1; }
    #actions Button { margin: 0 1; }
    #servers { height: auto; padding: 0 1; align: left middle; }
    #servers Button { margin: 0 1; min-width: 10; }
    #server_status { width: 1fr; height: auto; content-align: left middle; }
    #log { height: 10; border: round $secondary; }
    #preview_bar { height: auto; align: left middle; padding: 0 1; }
    #preview_bar Button { margin: 0 1; min-width: 8; }
    #preview_info { width: 1fr; content-align: left middle; }
    #preview { height: 1fr; border: round $primary; }
    .running { color: $warning; }
    """
    BINDINGS: ClassVar = [
        ("r", "refresh", "새로고침"),
        ("s", "cycle_sort", "정렬 변경"),
        ("n", "next_page", "다음"),
        ("p", "prev_page", "이전"),
        ("q", "quit", "종료"),
    ]

    _PREVIEW_LIMIT = 50  # 페이지당 종목 수 (테이블은 박스 내 스크롤)

    def __init__(self) -> None:
        super().__init__()
        self._sort_keys = list(admin_status.PREVIEW_SORTS.keys())
        self._sort_idx = 0
        self._page = 0  # 0-based
        self._total = 0
        self._servers = ServerControl()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static(id="status")
            with Horizontal(id="actions"):
                yield Button("리포트 수집", id="ingest", variant="primary")
                yield Button("유니버스 스냅샷", id="universe", variant="primary")
                yield Button("성장 배치", id="growth", variant="primary")
                yield Button("새로고침", id="refresh")
            with Horizontal(id="servers"):
                yield Static(id="server_status")
                yield Button("API 재기동", id="api_restart", variant="warning")
                yield Button("WEB 재기동", id="web_restart", variant="warning")
            yield Log(id="log", highlight=True)
            with Horizontal(id="preview_bar"):
                yield Button("◀ 이전", id="prev")
                yield Static(id="preview_info")
                yield Button("다음 ▶", id="next")
                yield Button("정렬: 매출YoY↓", id="sort")
            yield DataTable(id="preview")
        yield Footer()

    _log_handler: _LogHandler | None = None

    def on_mount(self) -> None:
        init_db()
        # 서비스 로그를 TUI 로그 패널로 라우팅
        handler = _LogHandler(self, self.query_one("#log", Log))
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s", "%H:%M:%S"))
        for name in ("app", "reporter"):
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        self._log_handler = handler

        table = self.query_one("#preview", DataTable)
        table.add_columns("종목", "시총(억)", "매출YoY", "모멘텀")
        self.action_refresh()
        # 서버가 스스로 죽거나(bind 실패·크래시) 하면 상태 패널이 stale 하지 않도록 주기 갱신.
        self.set_interval(3.0, self._refresh_server_status)

    def on_unmount(self) -> None:
        # 서버는 launchd 가 관리하므로 TUI 종료 시 건드리지 않는다(상시 유지).
        # 핸들러 누수 방지(다음 실행/테스트에 파괴된 위젯을 참조하지 않도록).
        if self._log_handler:
            for name in ("app", "reporter"):
                logging.getLogger(name).removeHandler(self._log_handler)
            self._log_handler = None

    def _refresh_server_status(self) -> None:
        # launchd 서비스 상태. URL 은 콜론 때문에 마크업 태그로 오해되지 않도록 평문으로 둔다.
        lines = []
        for s in self._servers.status():
            if not s.loaded:
                mark = f"[red]✗ 미등록[/red]  {s.url} (./launchd/install.sh 필요)"
            elif s.running:
                mark = f"[green]●[/green] 실행중(pid {s.pid})  {s.url}"
            else:
                mark = f"[yellow]○ 대기(재시작 중)  {s.url}[/yellow]"
            lines.append(f"{s.label}  {mark}")
        self.query_one("#server_status", Static).update("서버 (launchd 관리)\n" + "\n".join(lines))

    # --- 상태 ---
    def action_refresh(self) -> None:
        db = SessionLocal()
        try:
            counts = admin_status.table_counts(db)
            fresh = admin_status.freshness(db)
        finally:
            db.close()
        lines = [
            "[b]시스템 상태[/b]  (r=새로고침, q=종료)",
            "테이블 행수: "
            + "  ".join(f"{k}={v:,}" for k, v in counts.items()),
            f"최신 리포트: {fresh['latest_report_date']}   "
            f"유니버스 스냅샷: {fresh['latest_universe_date']} "
            f"({fresh['universe_today_rows']}종목)",
        ]
        self.query_one("#status", Static).update("\n".join(lines))
        self._refresh_server_status()
        self._load_preview()

    def _load_preview(self) -> None:
        """스몰캡 성장주 미리보기 — 선택 정렬·현재 페이지."""
        sort = self._sort_keys[self._sort_idx]
        db = SessionLocal()
        try:
            page = admin_status.screener_preview(
                db,
                sort=sort,
                limit=self._PREVIEW_LIMIT,
                offset=self._page * self._PREVIEW_LIMIT,
            )
        finally:
            db.close()
        self._total = page.total

        table = self.query_one("#preview", DataTable)
        table.clear()
        for r in page.rows:
            cap = f"{r.market_cap / 1e8:,.0f}" if r.market_cap else "—"
            ry = f"{r.revenue_yoy * 100:+.0f}%" if r.revenue_yoy is not None else "—"
            mm = f"{r.momentum_3m:+.0f}%" if r.momentum_3m is not None else "—"
            table.add_row(r.stock_name, cap, ry, mm)

        # 페이지/정렬 상태 표시 + 컨트롤 갱신
        total_pages = max(1, -(-self._total // self._PREVIEW_LIMIT))  # ceil
        start = self._page * self._PREVIEW_LIMIT + 1 if page.rows else 0
        end = start + len(page.rows) - 1 if page.rows else 0
        self.query_one("#preview_info", Static).update(
            f"[b]스몰캡 성장주[/b]  {start}-{end} / {self._total}  "
            f"(페이지 {self._page + 1}/{total_pages}, 정렬: {sort})"
        )
        self.query_one("#sort", Button).label = f"정렬: {sort}"
        self.query_one("#prev", Button).disabled = self._page <= 0
        self.query_one("#next", Button).disabled = (self._page + 1) >= total_pages

    # --- 정렬·페이지 ---
    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self._sort_keys)
        self._page = 0  # 정렬 바뀌면 첫 페이지로
        self._load_preview()

    def action_next_page(self) -> None:
        total_pages = max(1, -(-self._total // self._PREVIEW_LIMIT))
        if self._page + 1 < total_pages:
            self._page += 1
            self._load_preview()

    def action_prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._load_preview()

    _job_running = False
    _JOB_BUTTONS = ("ingest", "universe", "growth")

    # 잡 종류별 (시작 메시지, 실행 함수). 실행 함수는 (db) → 결과문자열.
    def _jobs(self) -> dict:
        settings = get_settings()

        def _ingest(db) -> str:
            n = ingest.ingest_reports(db, settings)
            ingest.build_market_brief(db, settings)
            bc = broadcast_ingest.ingest_broadcasts(db, settings)
            return f"신규 리포트 {n}건 · 브로드캐스트 {bc}건"

        return {
            "ingest": ("리포트 수집", _ingest),
            "universe": (
                "유니버스 스냅샷",
                lambda db: f"{universe_ingest.snapshot_universe(db, datetime.now().date())}종목",
            ),
            "growth": ("성장 배치", lambda db: str(growth_ingest.run_growth_batch(db))),
        }

    # --- 액션 (워커 스레드) ---
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "refresh":
            self.action_refresh()
        elif bid == "sort":
            self.action_cycle_sort()
        elif bid == "next":
            self.action_next_page()
        elif bid == "prev":
            self.action_prev_page()
        elif bid in ("api_restart", "web_restart"):
            self._handle_server_button(bid)
        elif bid in self._JOB_BUTTONS:
            if self._job_running:  # 실행 중엔 이중 크롤/GLM 방지
                self._log_line("⚠ 다른 작업이 실행 중입니다. 완료 후 다시 시도하세요.")
                return
            self._run_job(bid)

    def _handle_server_button(self, bid: str) -> None:
        key = bid.split("_")[0]  # api_restart → "api"
        self._log_line(self._servers.restart(key))
        self._refresh_server_status()

    def _log_line(self, msg: str) -> None:
        self.query_one("#log", Log).write_line(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _set_jobs_enabled(self, enabled: bool) -> None:
        self._job_running = not enabled
        for bid in self._JOB_BUTTONS:
            self.query_one(f"#{bid}", Button).disabled = not enabled

    @work(thread=True, exclusive=True, group="job")
    def _run_job(self, job_id: str) -> None:
        label, fn = self._jobs()[job_id]
        self.call_from_thread(self._set_jobs_enabled, False)
        self.call_from_thread(self._log_line, f"▶ {label} 시작…")
        db = SessionLocal()
        try:
            result = fn(db)
            self.call_from_thread(self._log_line, f"✔ {label} 완료: {result}")
        except Exception as e:
            self.call_from_thread(self._log_line, f"✖ {label} 실패: {e}")
        finally:
            db.close()
        self.call_from_thread(self.action_refresh)
        self.call_from_thread(self._set_jobs_enabled, True)


def main() -> None:
    AdminTUI().run()


if __name__ == "__main__":
    main()
