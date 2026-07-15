"""Admin TUI (Textual) — 수집 트리거 + 시스템 상태 모니터링 + 배포.

서비스 계층을 직접 호출한다(HTTP 미경유). 무거운 작업은 워커 스레드에서 돌려
UI 를 막지 않고, 진행 상황을 하단 로그 패널에 스트리밍한다.

화면은 상단 탭(개요·운영·서버/배포·스케줄·종목)으로 나뉜다. 숫자키 1~5 로 탭 전환.
로그 패널은 모든 탭 아래에 고정되어 어떤 작업의 진행도 한 곳에서 보인다.

실행: cd api && uv run reporter-tui
주의: 트리거는 실제 크롤/GLM/네이버 호출을 수행한다(라이브 자원 사용).
"""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Log,
    Static,
    TabbedContent,
    TabPane,
)

from app.db.session import SessionLocal, init_db
from app.scheduler import MANUAL_BATCHES
from app.services import (
    admin_status,
    company_service,
    fallback_store,
    ingest_log,
)
from app.services import server_control as sc
from app.services.schedule_control import ScheduleControl
from app.services.server_control import ProdDeploy, ServerControl, web_login_enabled


class TimeEditScreen(ModalScreen[str | None]):
    """HH:MM 발송 시각 입력 모달. 저장 시 'HH:MM' 문자열을, 취소 시 None 을 반환한다."""

    CSS = """
    TimeEditScreen { align: center middle; }
    #dialog { width: 44; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    #dialog Static { margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #edit_buttons { height: auto; align: center middle; }
    #edit_buttons Button { margin: 0 1; }
    """
    BINDINGS: ClassVar = [("escape", "cancel", "취소")]

    def __init__(self, suffix: str, desc: str, current: str) -> None:
        super().__init__()
        self._suffix = suffix
        self._desc = desc
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[b]{self._suffix}[/b] ({self._desc})\n발송 시각 (HH:MM, 월~금)")
            yield Input(value=self._current, placeholder="HH:MM", id="time_input")
            with Horizontal(id="edit_buttons"):
                yield Button("저장", id="save", variant="primary")
                yield Button("취소", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#time_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(self.query_one("#time_input", Input).value.strip())
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """예/아니오 확인 모달. 확인 시 True, 취소 시 False 를 반환한다(라이브 영향 작업 게이트)."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: round $error; background: $surface; padding: 1 2; }
    #dialog Static { margin-bottom: 1; }
    #confirm_buttons { height: auto; align: center middle; }
    #confirm_buttons Button { margin: 0 1; }
    """
    BINDINGS: ClassVar = [("escape", "cancel", "취소")]

    def __init__(self, title: str, body: str, ok_label: str = "진행") -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._ok_label = ok_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[b]{self._title}[/b]\n\n{self._body}")
            with Horizontal(id="confirm_buttons"):
                yield Button(self._ok_label, id="ok", variant="error")
                yield Button("취소", id="cancel", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()  # 기본 포커스는 안전한 취소

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class LogScreen(ModalScreen[None]):
    """서비스/배치 로그를 크게 보는 모달(3 depth). 로그 텍스트를 스크롤 가능한 Log 에 채운다."""

    CSS = """
    LogScreen { align: center middle; }
    #logbox { width: 90%; height: 80%; border: round $secondary; background: $surface; padding: 1; }
    #logtitle { height: auto; margin-bottom: 1; }
    """
    BINDINGS: ClassVar = [("escape", "close", "닫기")]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="logbox"):
            yield Static(f"[b]{self._title}[/b]  (esc=닫기)", id="logtitle")
            log = Log(id="logview", highlight=True)
            yield log

    def on_mount(self) -> None:
        self.query_one("#logview", Log).write(self._text)

    def action_close(self) -> None:
        self.dismiss(None)


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
    Tabs { dock: top; }
    #status { height: auto; border: round $accent; padding: 1; margin: 0 1; }
    .panel-title { height: auto; padding: 0 1; }
    .tbl { height: auto; max-height: 16; border: round $primary; margin: 0 1; }
    .tbl-warn { height: auto; max-height: 10; border: round $warning; margin: 0 1; }
    .bar { height: auto; align: left middle; padding: 0 1; }
    .bar Button { margin: 0 1; }
    .hint { width: 1fr; height: auto; content-align: left middle; }
    #batch_bar { height: auto; padding: 0 1; }
    #batch_bar Button { margin: 0 1; min-width: 16; }
    #server_status { width: 1fr; height: auto; content-align: left middle; }
    #deploy_hint { width: 1fr; height: auto; content-align: left middle; }
    #search_input { margin: 0 1; width: 60; }
    #detail { height: auto; border: round $accent; margin: 0 1; padding: 1; }
    #log { height: 10; border: round $secondary; dock: bottom; }
    """
    BINDINGS: ClassVar = [
        ("1", "show_tab('tab_overview')", "개요"),
        ("2", "show_tab('tab_ops')", "운영"),
        ("3", "show_tab('tab_deploy')", "서버/배포"),
        ("4", "show_tab('tab_schedule')", "스케줄"),
        ("5", "show_tab('tab_stocks')", "종목"),
        ("r", "refresh", "새로고침"),
        ("t", "toggle_job", "발송 on/off"),
        ("e", "edit_job", "시각 편집"),
        ("q", "quit", "종료"),
    ]

    _PREVIEW_LIMIT = 50

    def __init__(self) -> None:
        super().__init__()
        self._sort_keys = list(admin_status.PREVIEW_SORTS.keys())
        self._sort_idx = 0
        self._page = 0
        self._total = 0
        self._servers = ServerControl()
        self._prod = ProdDeploy()
        self._schedule = ScheduleControl()
        self._jobs_cache: list = []

    # ── 레이아웃 ─────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="tab_overview"):
            with TabPane("개요", id="tab_overview"), VerticalScroll():
                yield Static(id="status")
                yield Static("[b]DB 적재 현황[/b]", id="db_title", classes="panel-title")
                yield DataTable(id="db_status", classes="tbl")
            with TabPane("운영", id="tab_ops"), VerticalScroll():
                yield Static("[b]배치 수동 실행[/b]  (실제 크롤/LLM 호출)", classes="panel-title")
                with Horizontal(id="batch_bar"):
                    for key, label, _ in MANUAL_BATCHES:
                        yield Button(label, id=f"batch_{key}", variant="primary")
                yield Static(id="ingest_title", classes="panel-title")
                yield DataTable(id="ingest_history", classes="tbl")
                yield Static(id="fallback_title", classes="panel-title")
                yield DataTable(id="fallback", classes="tbl-warn")
            with TabPane("서버/배포", id="tab_deploy"), VerticalScroll():
                yield Static(id="server_status", classes="panel-title")
                with Horizontal(classes="bar"):
                    yield Button("WEB 빌드", id="web_build", variant="primary")
                    yield Button("API 재기동", id="api_restart", variant="warning")
                    yield Button("WEB 재기동", id="web_restart", variant="warning")
                with Horizontal(classes="bar"):
                    yield Button("API 로그", id="log_api")
                    yield Button("WEB 로그", id="log_web")
                    yield Button("worker 로그", id="log_worker")
                    yield Button("배치 로그", id="log_launchd")
                yield Static(id="deploy_hint", classes="panel-title")
                with Horizontal(classes="bar"):
                    yield Button("배포 미리보기", id="prod_preview")
                    yield Button("release 배포 ▶", id="prod_deploy", variant="error")
                    yield Button("CD 상태 확인", id="cd_status")
            with TabPane("스케줄", id="tab_schedule"), VerticalScroll():
                yield Static(id="schedule_hint", classes="panel-title")
                with Horizontal(classes="bar"):
                    yield Button("발송 on/off", id="job_toggle")
                    yield Button("시각 편집", id="job_edit", variant="primary")
                yield DataTable(id="schedule", classes="tbl")
            with TabPane("종목", id="tab_stocks"), VerticalScroll():
                yield Static("[b]종목 검색[/b]  코드/명 입력 후 Enter", classes="panel-title")
                yield Input(placeholder="예: 005930 또는 삼성전자", id="search_input")
                yield Static("검색 결과가 여기 표시됩니다.", id="detail")
                yield Static(id="preview_info", classes="panel-title")
                with Horizontal(classes="bar"):
                    yield Button("◀ 이전", id="prev")
                    yield Button("다음 ▶", id="next")
                    yield Button("정렬 변경", id="sort")
                yield DataTable(id="preview", classes="tbl")
        yield Log(id="log", highlight=True)
        yield Footer()

    _log_handler: _LogHandler | None = None

    def on_mount(self) -> None:
        init_db()
        handler = _LogHandler(self, self.query_one("#log", Log))
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s", "%H:%M:%S"))
        for name in ("app", "reporter"):
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        self._log_handler = handler

        self.query_one("#preview", DataTable).add_columns("종목", "시총(억)", "매출YoY", "모멘텀")
        sched = self.query_one("#schedule", DataTable)
        sched.add_columns("발송 잡", "시각", "상태")
        sched.cursor_type = "row"
        self.query_one("#schedule_hint", Static).update(
            "[b]발송 스케줄[/b] (launchd · 월~금)  행 선택 후  t=on/off  e=시각편집"
        )
        self.query_one("#fallback", DataTable).add_columns("시각", "종류", "사유", "대상")
        self.query_one("#db_status", DataTable).add_columns("테이블", "행수", "최신 업데이트")
        self.query_one("#ingest_history", DataTable).add_columns("시각", "작업", "결과", "건수", "소요")

        self.action_refresh()
        self.set_interval(3.0, self._refresh_server_status)

    def on_unmount(self) -> None:
        if self._log_handler:
            for name in ("app", "reporter"):
                logging.getLogger(name).removeHandler(self._log_handler)
            self._log_handler = None

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    # ── 상태 갱신 ────────────────────────────────────────────────────────
    def action_refresh(self) -> None:
        db = SessionLocal()
        try:
            counts = admin_status.table_counts(db)
            fresh = admin_status.freshness(db)
        finally:
            db.close()
        lines = [
            "[b]시스템 상태[/b]  (1~5=탭, r=새로고침, q=종료)",
            "테이블 행수: " + "  ".join(f"{k}={v:,}" for k, v in counts.items()),
            f"최신 리포트: {fresh['latest_report_date']}   "
            f"유니버스 스냅샷: {fresh['latest_universe_date']} "
            f"({fresh['universe_today_rows']}종목)",
        ]
        self.query_one("#status", Static).update("\n".join(lines))
        self._refresh_server_status()
        self._load_schedule()
        self._load_db_status()
        self._load_ingest_history()
        self._load_fallbacks()
        self._load_preview()

    def _refresh_server_status(self) -> None:
        lines = ["[b]로컬(dev) 서버[/b] (launchd 관리)"]
        for s in self._servers.status():
            if not s.loaded:
                mark = f"[red]✗ 미등록[/red]  {s.url} (./launchd/install.sh 필요)"
            elif s.running:
                mark = f"[green]●[/green] 실행중(pid {s.pid})  {s.url}"
            else:
                mark = f"[yellow]○ 대기(재시작 중)  {s.url}[/yellow]"
            lines.append(f"{s.label}  {mark}")
        lines.append(self._login_gate_line())
        with contextlib.suppress(Exception):  # 탭 미마운트 타이밍 방어
            self.query_one("#server_status", Static).update("\n".join(lines))
            self.query_one("#deploy_hint", Static).update(
                "[b]배포(prod)[/b] main→release push → CD(self-hosted runner) 자동 배포"
            )

    @staticmethod
    def _login_gate_line() -> str:
        enabled = web_login_enabled()
        if enabled is None:
            return "웹 로그인  [dim]? web/.env.local 없음[/dim]"
        if enabled:
            return "웹 로그인  [green]● 켜짐[/green] (LOGIN_PASSWORD 설정됨)"
        return "웹 로그인  [yellow]○ 꺼짐[/yellow] (LOGIN_PASSWORD 미설정 — 게이트 열림)"

    def _load_schedule(self) -> None:
        table = self.query_one("#schedule", DataTable)
        prev_row = table.cursor_row if table.row_count else 0
        self._jobs_cache = self._schedule.jobs()
        table.clear()
        for job in self._jobs_cache:
            if not job.enabled:
                state = "[dim]⏸ 꺼짐[/dim]"
            elif job.loaded:
                state = "[green]● 켜짐[/green]"
            else:
                state = "[yellow]○ 미로드[/yellow]"
            table.add_row(f"{job.suffix}  [dim]{job.desc}[/dim]", job.time_label, state)
        if self._jobs_cache:
            table.move_cursor(row=min(prev_row, len(self._jobs_cache) - 1))

    def _load_db_status(self) -> None:
        db = SessionLocal()
        try:
            statuses = admin_status.db_status(db)
            done, total = admin_status.backfill_progress(db)
        finally:
            db.close()
        pct = f"{done / total * 100:.0f}%" if total else "—"
        self.query_one("#db_title", Static).update(
            f"[b]DB 적재 현황[/b]  (최신순)  10년 일봉 백필: {done:,}/{total:,} ({pct})"
        )
        table = self.query_one("#db_status", DataTable)
        table.clear()
        for s in statuses:
            table.add_row(s.name, f"{s.rows:,}", s.latest)

    def _load_ingest_history(self) -> None:
        db = SessionLocal()
        try:
            rows = ingest_log.recent(db, limit=30)
            fail_24h = ingest_log.recent_failure_count(db, since_hours=24)
        finally:
            db.close()
        if fail_24h > 0:
            title = f"[b]적재 이력[/b]  [red]최근 24h 실패 {fail_24h}건 ✖[/red]  (최근 30건)"
        else:
            title = "[b]적재 이력[/b]  [green]최근 24h 실패 없음 ✔[/green]  (최근 30건)"
        self.query_one("#ingest_title", Static).update(title)
        table = self.query_one("#ingest_history", DataTable)
        table.clear()
        for r in rows:
            ts = r.ts.astimezone().strftime("%m-%d %H:%M") if r.ts else "—"
            label = ingest_log.JOB_LABELS.get(r.job, r.job)
            ok = r.status == "ok"
            mark = "[green]✔[/green]" if ok else "[red]✖[/red]"
            dur = f"{r.duration_ms / 1000:.1f}s" if r.duration_ms else "—"
            job_cell = f"{mark} {label}" if ok else f"{mark} [red b]{label}[/red b]"
            detail_cell = r.detail[:48] if ok else f"[red]{r.detail[:48]}[/red]"
            table.add_row(ts, job_cell, detail_cell, f"{r.rows:,}", dur)

    def _load_fallbacks(self) -> None:
        db = SessionLocal()
        try:
            recent = fallback_store.recent_fallbacks(db, limit=30)
            counts = fallback_store.fallback_counts(db, since_hours=24)
        finally:
            db.close()
        if counts:
            summary = "  ".join(f"{c.key}={c.count}" for c in counts)
            title = f"[b]폴백 이력[/b]  최근 24h: {summary}"
        else:
            title = "[b]폴백 이력[/b]  최근 24h 폴백 없음 ✓"
        self.query_one("#fallback_title", Static).update(title)
        table = self.query_one("#fallback", DataTable)
        table.clear()
        for r in recent:
            ts = r.ts.astimezone().strftime("%m-%d %H:%M") if r.ts else "—"
            table.add_row(ts, r.key, r.reason[:60], r.detail[:24])

    def _load_preview(self) -> None:
        sort = self._sort_keys[self._sort_idx]
        db = SessionLocal()
        try:
            page = admin_status.screener_preview(
                db, sort=sort, limit=self._PREVIEW_LIMIT, offset=self._page * self._PREVIEW_LIMIT
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
        total_pages = max(1, -(-self._total // self._PREVIEW_LIMIT))
        start = self._page * self._PREVIEW_LIMIT + 1 if page.rows else 0
        end = start + len(page.rows) - 1 if page.rows else 0
        self.query_one("#preview_info", Static).update(
            f"[b]스몰캡 성장주[/b]  {start}-{end} / {self._total}  "
            f"(페이지 {self._page + 1}/{total_pages}, 정렬: {sort})"
        )
        self.query_one("#prev", Button).disabled = self._page <= 0
        self.query_one("#next", Button).disabled = (self._page + 1) >= total_pages

    # ── 발송 스케줄 ──────────────────────────────────────────────────────
    def _selected_job(self):
        table = self.query_one("#schedule", DataTable)
        row = table.cursor_row
        if not self._jobs_cache or row is None or row >= len(self._jobs_cache):
            self._log_line("⚠ 스케줄 표에서 잡을 먼저 선택하세요.")
            return None
        return self._jobs_cache[row]

    def action_toggle_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        self._log_line(self._schedule.toggle(job.suffix, job.enabled))
        self._load_schedule()

    def action_edit_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        def _apply(value: str | None) -> None:
            if not value:
                return
            self._log_line(self._apply_time_edit(job.suffix, value))
            self._load_schedule()

        self.push_screen(TimeEditScreen(job.suffix, job.desc, job.time_label), _apply)

    def _apply_time_edit(self, suffix: str, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return f"시각 형식이 올바르지 않습니다: '{value}' (HH:MM 로 입력)"
        return self._schedule.set_time(suffix, int(parts[0]), int(parts[1]))

    # ── 정렬·페이지 ──────────────────────────────────────────────────────
    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self._sort_keys)
        self._page = 0
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

    # ── 종목 검색 ────────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search_input":
            self._run_stock_search(event.value.strip())

    @work(thread=True, exclusive=True, group="search")
    def _run_stock_search(self, q: str) -> None:
        if not q:
            return
        db = SessionLocal()
        try:
            hits = company_service.search_candidates(db, q)
            text = self._format_stock_detail(db, hits, q)
        except Exception as e:  # 검색 실패는 상세 패널에 표기(앱은 계속)
            text = f"[red]검색 실패: {e}[/red]"
        finally:
            db.close()
        self.call_from_thread(self.query_one("#detail", Static).update, text)

    def _format_stock_detail(self, db, hits: list, q: str) -> str:
        if not hits:
            return f"'{q}' 검색 결과 없음."
        if len(hits) > 1:
            # 다중 후보 → 목록만(첫 후보 상세는 아래에 덧붙임).
            lines = [f"[b]'{q}' 후보 {len(hits)}건[/b] (정확히 입력하면 상세 표시):"]
            for code, name, market, cap in hits[:12]:
                cap_s = f"{cap / 1e8:,.0f}억" if cap else "—"
                lines.append(f"  {code}  {name}  [{market}]  {cap_s}")
            return "\n".join(lines)
        code, name, market, cap = hits[0]
        snap = company_service.latest_snapshot(db, code)
        gm = company_service.growth_metric(db, code)
        fins = company_service.financials_rows(db, code)
        themes = company_service.theme_names(db, code)
        cap_s = f"{cap / 1e8:,.0f}억" if cap else "—"
        close = f"{snap.close_price:,}" if snap and snap.close_price else "—"
        mom = f"{snap.momentum_3m:+.0f}%" if snap and snap.momentum_3m is not None else "—"
        rs = snap.rs_rating if snap and snap.rs_rating is not None else "—"
        ry = f"{gm.revenue_yoy * 100:+.0f}%" if gm and gm.revenue_yoy is not None else "—"
        fin_latest = fins[0].period if fins else "—"
        return (
            f"[b]{name}[/b] ({code})  [{market}]  시총 {cap_s}\n"
            f"현재가 {close}   3M모멘텀 {mom}   RS {rs}   매출YoY {ry}\n"
            f"재무 최신분기 {fin_latest} ({len(fins)}개)   테마 {', '.join(themes[:5]) or '—'}"
        )

    # ── 배치·서버·배포 (워커 스레드) ────────────────────────────────────
    _busy = False  # 배치/빌드/배포 상호배제(이중 실행 방지)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid in ("api_restart", "web_restart"):
            key = bid.split("_")[0]
            self._log_line(self._servers.restart(key))
            self._refresh_server_status()
        elif bid == "web_build":
            self._guarded(self._run_web_build)
        elif bid in ("log_api", "log_web", "log_worker", "log_launchd"):
            self._show_log(bid.split("_", 1)[1])
        elif bid == "prod_preview":
            self._run_prod_preview()
        elif bid == "cd_status":
            self._run_cd_status()
        elif bid == "prod_deploy":
            self._confirm_prod_deploy()
        elif bid == "job_toggle":
            self.action_toggle_job()
        elif bid == "job_edit":
            self.action_edit_job()
        elif bid == "sort":
            self.action_cycle_sort()
        elif bid == "next":
            self.action_next_page()
        elif bid == "prev":
            self.action_prev_page()
        elif bid.startswith("batch_"):
            self._guarded(lambda: self._run_batch(bid[len("batch_"):]))

    def _guarded(self, fn) -> None:
        """배치/빌드/배포는 서로 배타 실행 — 이중 크롤/GLM/빌드 방지."""
        if self._busy:
            self._log_line("⚠ 다른 작업이 실행 중입니다. 완료 후 다시 시도하세요.")
            return
        fn()

    # --- 서비스 로그 뷰어 ---
    @work(thread=True, exclusive=True, group="logview")
    def _show_log(self, key: str) -> None:
        self.call_from_thread(self._log_line, f"▶ {key} 로그 조회…")
        try:
            text = sc.worker_log(60) if key == "worker" else sc.tail_service_log(key, 60)
        except Exception as e:
            text = f"로그 조회 실패: {e}"
        self.call_from_thread(self.push_screen, LogScreen(f"{key} 로그 (최근 60줄)", text))

    # --- WEB 빌드 ---
    @work(thread=True, exclusive=True, group="busy")
    def _run_web_build(self) -> None:
        self.call_from_thread(self._set_busy, True)
        self.call_from_thread(self._log_line, "▶ WEB 빌드 시작… (수십 초 걸립니다)")
        try:
            msg = self._servers.build_web()
        except Exception as e:
            msg = f"✖ WEB 빌드 실패: {e}"
        self.call_from_thread(self._log_line, msg)
        self.call_from_thread(self._set_busy, False)

    # --- 배치 수동 실행 ---
    @work(thread=True, exclusive=True, group="busy")
    def _run_batch(self, key: str) -> None:
        entry = next((e for e in MANUAL_BATCHES if e[0] == key), None)
        if entry is None:
            self.call_from_thread(self._log_line, f"⚠ 알 수 없는 배치: {key}")
            return
        _, label, fn = entry
        self.call_from_thread(self._set_busy, True)
        self.call_from_thread(self._log_line, f"▶ {label} 시작…")
        start = time.monotonic()
        try:
            result = fn()
            self.call_from_thread(self._log_line, f"✔ {label} 완료: {result}")
            ingest_log.record(
                None, f"manual_{key}", detail=str(result)[:200],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            self.call_from_thread(self._log_line, f"✖ {label} 실패: {e}")
            ingest_log.record(
                None, f"manual_{key}", status="fail", detail=str(e)[:200],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        self.call_from_thread(self.action_refresh)
        self.call_from_thread(self._set_busy, False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        # 배치·빌드 버튼 일괄 비활성(진행 중 이중 실행 차단).
        for btn in self.query("#batch_bar Button"):
            btn.disabled = busy
        with contextlib.suppress(Exception):
            self.query_one("#web_build", Button).disabled = busy

    # --- 배포 ---
    @work(thread=True, exclusive=True, group="prod")
    def _run_prod_preview(self) -> None:
        self.call_from_thread(self._log_line, "▶ 배포 미리보기(main→release 대상 조회)…")
        try:
            msg = self._prod.preview()
        except Exception as e:
            msg = f"✖ 배포 미리보기 실패: {e}"
        self.call_from_thread(self._log_line, msg)

    @work(thread=True, exclusive=True, group="prod")
    def _run_cd_status(self) -> None:
        self.call_from_thread(self._log_line, "▶ CD 상태 조회…")
        try:
            msg = self._prod.cd_status()
        except Exception as e:
            msg = f"✖ CD 상태 조회 실패: {e}"
        self.call_from_thread(self._log_line, msg)

    def _confirm_prod_deploy(self) -> None:
        if self._busy:
            self._log_line("⚠ 다른 작업이 실행 중입니다.")
            return

        def _on_confirm(ok: bool | None) -> None:
            if ok:
                self._run_prod_deploy()

        self.push_screen(
            ConfirmScreen(
                "release 배포를 진행할까요?",
                "main 의 커밋을 release 로 push 해 프로덕션 CD(자동 배포)를 트리거합니다.\n"
                "라이브 서비스에 반영됩니다.",
                ok_label="배포",
            ),
            _on_confirm,
        )

    @work(thread=True, exclusive=True, group="prod")
    def _run_prod_deploy(self) -> None:
        self.call_from_thread(self._log_line, "▶ release 배포 트리거 중(main→release push)…")
        try:
            msg = self._prod.deploy()
        except Exception as e:
            msg = f"✖ release 배포 실패: {e}"
        self.call_from_thread(self._log_line, msg)
        # push 성공 시 CD 진행 상황을 몇 차례 폴링해 로그에 자동 표시(배포 완료 확인 편의).
        if "트리거됨" in msg:
            self._poll_cd()

    def _poll_cd(self) -> None:
        for _ in range(20):  # 최대 ~5분(15s 간격)
            time.sleep(15)
            try:
                status = self._prod.cd_status()
            except Exception:
                break
            self.call_from_thread(self._log_line, status)
            if "[진행중" not in status:
                break

    def _log_line(self, msg: str) -> None:
        self.query_one("#log", Log).write_line(f"[{datetime.now():%H:%M:%S}] {msg}")


def main() -> None:
    AdminTUI().run()


if __name__ == "__main__":
    main()
