"""Admin TUI (Textual) — v67 redesign.

7 tabs: 현황, 배치, 적재이력, 모니터링(로그), 발송스케줄, 릴리스, 종목.
Centralized on_key filter, Option-as-Meta detection, lock model,
subprocess batch orchestration, audit, signal handling.

Run: cd api && uv run reporter-tui
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import fcntl
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, Literal

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from app.admin_paths import (
    HEARTBEAT_FILE_TEMPLATE,
    LOCK_FILE,
    LOG_DIR,
    PID_FILE_TEMPLATE,
    RUN_DIR,
)
from app.batch_meta import BATCH_KEY_TO_LOG_JOB, BATCH_META
from app.batch_meta import MANUAL_BATCHES_KEYS as MANUAL_BATCHES
from app.db.session import SessionLocal, init_db
from app.services import (
    admin_status,
    company_service,
    ingest_log,
)
from app.services import server_control as sc
from app.services.admin_audit import audit, cleanup_audit
from app.services.schedule_control import ScheduleControl
from app.services.server_control import ProdDeploy, ServerControl, web_login_enabled

logger = logging.getLogger("admin_tui")

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_HEARTBEAT_TIMEOUT = 180
TOAST_SHORT_SECONDS = 2
TOAST_LONG_SECONDS = 4
FORCE_RELEASE_LOCK_TIMEOUT = 5.0
LOG_READ_CHUNK_LINES = 100
AUDIT_CLEANUP_INTERVAL_SECONDS = 1800  # 30 min
BATCH_LOCK_POLL_SECONDS = 0.5
BATCH_LOCK_POLL_MAX_ATTEMPTS = 30  # max 15s
KILL_ATTEMPT_LIMIT = 3
ORPHAN_SIGTERM_WAIT_SECONDS = 2.0
_PREVIEW_LIMIT = 50

# ── Helpers ────────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(UTC)


def _pid_path(key: str) -> Path:
    return RUN_DIR / PID_FILE_TEMPLATE.format(key=key)


def _heartbeat_path(key: str) -> Path:
    return RUN_DIR / HEARTBEAT_FILE_TEMPLATE.format(key=key)


def _log_path(key: str) -> Path:
    return LOG_DIR / f"batch_{key}_{utcnow().strftime('%Y%m%d_%H%M%S')}.log"


def _parse_ps_etime(value: str) -> float:
    value = value.strip()
    if value.startswith("+"):
        value = value[1:]
    parts = value.split("-")
    if len(parts) == 2:
        days = int(parts[0])
        rest = parts[1]
    else:
        days = 0
        rest = parts[0]
    segments = rest.split(":")
    if len(segments) == 3:
        hours, minutes, seconds = map(int, segments)
    elif len(segments) == 2:
        hours = 0
        minutes, seconds = map(int, segments)
    elif len(segments) == 1:
        hours = minutes = 0
        seconds = int(segments[0].split(".")[0])
    else:
        raise ValueError(f"Unexpected etime format: {value}")
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


# ── Data classes ───────────────────────────────────────────────────────────


@dataclass
class RunningState:
    key: str
    started_at: datetime
    action_name: str
    status: Literal["running", "cancelling"]
    progress: str = "시작"
    pct: int = 0
    last_heartbeat: datetime = field(default_factory=utcnow)
    log_drops: int = 0
    log_path: Path | None = None


@dataclass
class LastRunResult:
    key: str
    returncode: int
    message: str
    ts: datetime
    log_drops: int = 0


# ── Screens ───────────────────────────────────────────────────────────────


class ConfirmScreen(ModalScreen):
    """확인 모달 — buttons=[(label, value), ...] API.

    value 가 False 면 취소로 간주한다. Esc 도 False 를 반환한다.
    """

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: round $error; background: $surface; padding: 1 2; }
    #dialog Static { margin-bottom: 1; }
    #confirm_buttons { height: auto; align: center middle; }
    #confirm_buttons Button { margin: 0 1; }
    """
    BINDINGS: ClassVar = [("escape", "cancel", "취소")]

    def __init__(self, message: str, buttons: list[tuple[str, object]] | None = None) -> None:
        super().__init__()
        self._message = message
        self._buttons = buttons or [("확인", True), ("취소", False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._message)
            with Horizontal(id="confirm_buttons"):
                for label, _ in self._buttons:
                    yield Button(label)
                # 마지막 버튼에 포커스(취소 기본)
                if len(self._buttons) > 1:
                    self._cancel_btn_idx = len(self._buttons) - 1

    def on_mount(self) -> None:
        if len(self._buttons) > 1:
            btns = self.query(Button)
            if len(btns) > self._cancel_btn_idx:
                btns[self._cancel_btn_idx].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        for label, value in self._buttons:
            if event.button.label == label:
                self.dismiss(value)
                return
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen):
    """도움말 모달 — 9개 섹션."""

    CSS = """
    HelpScreen { align: center middle; }
    #helpbox { width: 90%; height: 90%; border: round $secondary; background: $surface; padding: 1; }
    #helptitle { height: auto; margin-bottom: 1; text-style: bold; }
    #helpscroll { height: 1fr; }
    #helpscroll Static { margin-bottom: 1; }
    """
    BINDINGS: ClassVar = [("escape", "close", "닫기")]

    def compose(self) -> ComposeResult:
        with Vertical(id="helpbox"):
            yield Static("Admin TUI 도움말  (esc=닫기)", id="helptitle")
            with VerticalScroll(id="helpscroll"):
                yield Static(self._help_text())

    def action_close(self) -> None:
        self.dismiss(None)

    @staticmethod
    def _help_text() -> str:
        return """[b]1. 시작 가이드[/b]

Admin TUI는 reporter 서비스(API/web/worker)를 터미널에서 관리하는 도구입니다.

[b]7개 탭의 목적[/b]
| 탭 | 목적 |
|---|---|
| 현황 | 현재 실행 중인 배치, lock 상태, 최근 실행 결과를 한눈에 봅니다. |
| 배치 | daily_batch, 재무 백필, 릴리스 배포 등 작업을 실행합니다. |
| 적재이력 | 과거 배치 실행 이력과 재무 데이터 적재 상세를 봅니다. |
| 모니터링(로그) | API/WEB 로그를 실시간으로 tailing하고 레벨별로 필터링합니다. |
| 발송스케줄 | Telegram 등 일일 리포트 발송 시간과 채널을 관리합니다. |
| 릴리스 | API/WEB 재기동, WEB 빌드, 릴리스 배포/롤백을 수행합니다. |
| 종목 | 보유 종목의 테크노펀더멘탈/밸류 스코어를 조회합니다. |

[b]2. 주요 버튼[/b]
| 위치 | 버튼 | 설명 |
|---|---|---|
| Header 아래 | 1~7 현황/배치/.../종목 | 탭 전환 |
| Header 아래 | 새로고침(r) | 현재 탭 새로고침 |
| Header 아래 | 검색(/) | 검색 Input에 포커스 |
| Header 아래 | 중단(Ctrl+X) | 실행 중인 작업 취소 시도 |
| Header 아래 | 강제중단 | 취소 실패 시 강제 해제 |
| Header 아래 | lock 해제 | cross-instance lock 강제 해제 |
| Header 아래 | 도움말(F1) | 이 화면 열기 |
| 모니터링 탭 | health(h) | API/WEB health check |
| 모니터링 탭 | trace/debug/info/warn/error | 로그 레벨 토글 |
| 스케줄 탭 | 편집(e) | 선택 행 편집 |
| 스케줄 탭 | 신규(n) | 신규 추가 |
| 릴리스 탭 | WEB 빌드 / API 재기동 / WEB 재기동 / 배포 / 롤백 | 버튼 클릭 |

[b]3. 남은 키보드 단축키[/b]
| 단축키 | 동작 |
|---|---|
| Esc | 모달/ConfirmScreen 닫기 |
| q / Alt+Q | 종료 ConfirmScreen 열기 |
| Ctrl+X | 실행 중인 작업 취소 시도 |
| Ctrl+Shift+X | 취소 실패 시 강제 해제 |
| F1 | 도움말 열기 |

[b]4. Lock 모델[/b]
- operation.lock은 한 번에 하나의 batch_runner만 소유할 수 있습니다.
- TUI는 lock을 직접 걸지 않고 batch_runner가 획득했는지 감시합니다.
- lock이 점유 중이면 새 배치를 시작할 수 없습니다.

[b]5. detach(TUI만 종료)[/b]
- 실행 중인 작업이 있을 때 q를 누르면 "TUI만 종료(작업계속)" 옵션이 표시됩니다.
- 이 옵션을 선택하면 TUI는 닫히지만 batch_runner 프로세스는 백그라운드에서 계속 실행됩니다.

[b]6. 배치 exit code[/b]
| 코드 | 의미 |
|---|---|
| 0 | 정상 완료 |
| 2 | lock 점유로 인해 시작 불가 |
| 143 | SIGTERM에 의한 중단(취소) |
| 그 외 | 비정상 종료 |"""


class ScheduleEditScreen(ModalScreen):
    """발송스케줄 편집 모달 — dirty flag + 저장/취소/종료 확인."""

    CSS = """
    ScheduleEditScreen { align: center middle; }
    #dialog { width: 50; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    #dialog Static { margin-bottom: 1; }
    .field-row { height: auto; margin-bottom: 1; }
    .field-row Label { width: 12; }
    .field-row Input { width: 1fr; }
    #edit_buttons { height: auto; align: center middle; margin-top: 1; }
    #edit_buttons Button { margin: 0 1; }
    """
    BINDINGS: ClassVar = [("escape", "cancel_or_close", "닫기")]

    def __init__(
        self,
        suffix: str,
        desc: str,
        current_hour: int,
        current_minute: int,
        current_enabled: bool,
        is_new: bool = False,
    ) -> None:
        super().__init__()
        self._suffix = suffix
        self._desc = desc
        self._is_new = is_new
        self._original = {
            "hour": current_hour,
            "minute": current_minute,
            "enabled": current_enabled,
        }
        self._dirty = False

    def compose(self) -> ComposeResult:
        title = "신규 추가" if self._is_new else f"편집: {self._suffix}"
        with Vertical(id="dialog"):
            yield Static(f"[b]{title}[/b]  ({self._desc})", id="edit_title")
            with Horizontal(classes="field-row"):
                yield Label("시각 (HH:MM):")
                yield Input(
                    value=f"{self._original['hour']:02d}:{self._original['minute']:02d}",
                    placeholder="HH:MM",
                    id="time_input",
                )
            with Horizontal(classes="field-row"):
                yield Label("활성:")
                yield Checkbox(
                    value=self._original["enabled"],
                    id="enabled_checkbox",
                )
            with Horizontal(id="edit_buttons"):
                yield Button("저장", id="save", variant="primary")
                yield Button("취소", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#time_input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._check_dirty()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._check_dirty()

    def _check_dirty(self) -> None:
        time_val = self.query_one("#time_input", Input).value.strip()
        enabled = self.query_one("#enabled_checkbox", Checkbox).value
        try:
            parts = time_val.split(":")
            h, m = int(parts[0]), int(parts[1])
            dirty = (
                h != self._original["hour"]
                or m != self._original["minute"]
                or enabled != self._original["enabled"]
            )
        except (ValueError, IndexError):
            dirty = True
        self._dirty = dirty

    def _save(self) -> None:
        time_val = self.query_one("#time_input", Input).value.strip()
        enabled = self.query_one("#enabled_checkbox", Checkbox).value
        try:
            parts = time_val.split(":")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                self.notify(
                    "시각 형식이 올바르지 않습니다 (HH:MM)",
                    severity="error",
                    timeout=TOAST_SHORT_SECONDS,
                )
                self.query_one("#time_input", Input).focus()
                return
        except (ValueError, IndexError):
            self.notify(
                "시각 형식이 올바르지 않습니다 (HH:MM)",
                severity="error",
                timeout=TOAST_SHORT_SECONDS,
            )
            self.query_one("#time_input", Input).focus()
            return
        self.dismiss({"suffix": self._suffix, "hour": h, "minute": m, "enabled": enabled})

    def action_cancel_or_close(self) -> None:
        if self._dirty:
            self._confirm_discard()
        else:
            self.dismiss(None)

    def _confirm_discard(self) -> None:
        async def _on_confirm(value: object) -> None:
            if value == "save":
                self._save()
            elif value == "discard":
                self.dismiss(None)
            # "cancel" → do nothing, stay in edit modal

        self.push_screen(
            ConfirmScreen(
                "변경사항이 있습니다. 어떻게 할까요?",
                buttons=[("저장", "save"), ("저장하지 않음", "discard"), ("취소", False)],
            ),
            _on_confirm,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.action_cancel_or_close()

    def action_save_shortcut(self) -> None:
        """Alt+S: 편집 모달 전용 저장."""
        self._save()


class LogScreen(ModalScreen):
    """서비스/배치 로그를 크게 보는 모달."""

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


# ── Main App ──────────────────────────────────────────────────────────────


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
    #meta_warning { height: auto; background: $warning; color: $text; padding: 0 1; text-align: center; }
    #meta_warning Button { margin-left: 1; }
    #lock_status { height: auto; padding: 0 1; }
    #progress_bar { height: auto; padding: 0 1; }
    #results_table { height: auto; max-height: 10; border: round $accent; margin: 0 1; }
    .no-search-hint { height: auto; padding: 0 1; color: $text-muted; }
    #batch_table { height: auto; max-height: 20; border: round $primary; margin: 0 1; }
    #log_filter_bar { height: auto; padding: 0 1; }
    #log_filter_bar Button { margin: 0 1; min-width: 6; }
    #log_content { height: 1fr; border: round $secondary; margin: 0 1; }
    #release_info { height: auto; border: round $accent; margin: 0 1; padding: 1; }
    #release_buttons { height: auto; padding: 0 1; }
    #release_buttons Button { margin: 0 1; min-width: 14; }
    """

    # ── State ──────────────────────────────────────────────────────────
    _state: RunningState | None = None
    _last_returncode: int | None = None

    _detaching = False
    _killing = False
    _kill_attempts = 0
    _cancel_failure_auto_reset_task: asyncio.Task | None = None
    _cleanup_audit_task: asyncio.Task | None = None
    _current_subprocess: asyncio.subprocess.Process | None = None
    _reader_task: asyncio.Task | None = None
    _monitor_task: asyncio.Task | None = None
    _watcher_task: asyncio.Task | None = None
    _option_as_meta_detected: bool | None = None

    def __init__(self) -> None:
        super().__init__()
        self._last_results: deque[LastRunResult] = deque(maxlen=10)
        self._process_exited_event = asyncio.Event()
        self._orchestrator_stop_event = asyncio.Event()
        self._detach_event = asyncio.Event()
        self._cleanup_complete_event = asyncio.Event()
        self._detach_complete_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._batch_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._registered_signals: list[int] = []
        self._original_signal_handlers: dict[int, Any] = {}
        self._log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10000)
        self._background_tasks: set[asyncio.Task] = set()
        self._sort_keys = list(admin_status.PREVIEW_SORTS.keys())
        self._sort_idx = 0
        self._page = 0
        self._total = 0
        self._servers = ServerControl()
        self._prod = ProdDeploy()
        self._schedule = ScheduleControl()
        self._jobs_cache: list = []
        self._log_levels: dict[str, bool] = {
            "trace": False,
            "debug": False,
            "info": True,
            "warn": True,
            "error": True,
        }

    # ── Layout ─────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="meta_warning")
        # 탭 전환 버튼
        with Horizontal(id="tab_switch_bar"):
            yield Button("1 현황", id="tab_btn_overview", variant="primary")
            yield Button("2 배치", id="tab_btn_batch")
            yield Button("3 적재", id="tab_btn_ingest")
            yield Button("4 모니터링", id="tab_btn_log")
            yield Button("5 스케줄", id="tab_btn_schedule")
            yield Button("6 릴리스", id="tab_btn_release")
            yield Button("7 종목", id="tab_btn_stocks")
        # 전역 작업 버튼
        with Horizontal(id="global_action_bar"):
            yield Button("🔃 새로고침(r)", id="btn_refresh", variant="default")
            yield Button("🔍 검색(/)", id="btn_search")
            yield Button("⏹ 중단(Ctrl+X)", id="btn_cancel")
            yield Button("⚡ 강제중단", id="btn_force_cancel")
            yield Button("🔓 lock 해제", id="btn_lock_release")
            yield Button("❓ 도움말(F1)", id="btn_help")
        with TabbedContent(initial="tab_overview"):
            # Tab 1: 현황
            with TabPane("현황", id="tab_overview"), VerticalScroll():
                yield Static(id="status")
                yield Static(id="lock_status")
                yield Static(id="progress_bar")
                yield Static("[b]로컬 서버 상태[/b]", classes="panel-title")
                yield Static(id="server_status")
                yield Static("[b]최근 실행 결과[/b]", classes="panel-title")
                yield DataTable(id="results_table", classes="tbl")
                yield Static("[b]DB 적재 현황[/b]", id="db_title", classes="panel-title")
                yield DataTable(id="db_status", classes="tbl")
            # Tab 2: 배치
            with TabPane("배치", id="tab_batch"), VerticalScroll():
                yield Static(
                    "[b]배치 수동 실행[/b]  Alt+/ 검색 | Enter 실행 | Ctrl+X 중단 | r/Alt+R 새로고침",
                    classes="panel-title",
                )
                yield Input(placeholder="검색...", id="batch_search", classes="no-search-hint")
                yield DataTable(id="batch_table", classes="tbl")
            # Tab 3: 적재이력
            with TabPane("적재이력", id="tab_ingest"), VerticalScroll():
                yield Static(
                    "[b]적재 이력[/b]  Alt+/ 필터 | r/Alt+R 새로고침",
                    id="ingest_title",
                    classes="panel-title",
                )
                yield Input(placeholder="필터...", id="ingest_filter")
                yield DataTable(id="ingest_history", classes="tbl")
            # Tab 4: 모니터링(로그)
            with TabPane("모니터링(로그)", id="tab_log"), VerticalScroll():
                yield Static(
                    "[b]모니터링(로그)[/b]  Alt+/ 검색 | h/Alt+H health", classes="panel-title"
                )
                with Horizontal(id="log_filter_bar"):
                    yield Button("health(h)", id="log_health", variant="default")
                    yield Button("trace", id="log_trace", variant="default")
                    yield Button("debug", id="log_debug", variant="default")
                    yield Button("info", id="log_info", variant="primary")
                    yield Button("warn", id="log_warn", variant="warning")
                    yield Button("error", id="log_error", variant="error")
                yield RichLog(id="log_content", highlight=True, max_lines=1000)
            # Tab 5: 발송스케줄
            with TabPane("발송스케줄", id="tab_schedule"), VerticalScroll():
                yield Static(
                    "[b]발송스케줄[/b]  e/Alt+E 편집 | n/Alt+N 신규 | r/Alt+R 새로고침",
                    classes="panel-title",
                )
                with Horizontal(id="schedule_action_bar"):
                    yield Button("✏ 편집(e)", id="btn_edit_job")
                    yield Button("+ 신규(n)", id="btn_new_job")
                yield Static("이 탭은 검색을 지원하지 않습니다", classes="no-search-hint")
                yield DataTable(id="schedule", classes="tbl")
            # Tab 6: 릴리스
            with TabPane("릴리스", id="tab_release"), VerticalScroll():
                yield Static(
                    "[b]릴리스[/b]  b/Alt+B WEB 빌드 | a/Alt+A API 재기동 | w/Alt+W WEB 재기동",
                    classes="panel-title",
                )
                yield Static("이 탭은 검색을 지원하지 않습니다", classes="no-search-hint")
                yield Static(id="release_info")
                with Horizontal(id="release_buttons"):
                    yield Button("WEB 빌드", id="web_build", variant="primary")
                    yield Button("API 재기동", id="api_restart", variant="warning")
                    yield Button("WEB 재기동", id="web_restart", variant="warning")
                    yield Button("릴리스 배포", id="prod_deploy", variant="error")
                    yield Button("롤백", id="prod_rollback", variant="error")
                yield Static(id="deploy_hint", classes="panel-title")
            # Tab 7: 종목
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

    # ── Lifecycle ──────────────────────────────────────────────────────
    def on_mount(self) -> None:
        init_db()
        self._detect_option_as_meta()
        if self._option_as_meta_detected is False:
            self._show_meta_warning_banner()

        pass

        self.query_one("#preview", DataTable).add_columns("종목", "시총(억)", "매출YoY", "모멘텀")
        sched = self.query_one("#schedule", DataTable)
        sched.add_columns("ID", "시각", "채널", "내용요약", "활성")
        sched.cursor_type = "row"
        self.query_one("#db_status", DataTable).add_columns("테이블", "행수", "최신 업데이트")
        self.query_one("#ingest_history", DataTable).add_columns(
            "시각", "작업", "결과", "건수", "소요"
        )
        self.query_one("#results_table", DataTable).add_columns(
            "시간", "작업", "상태", "exit", "log_drops"
        )
        self.query_one("#batch_table", DataTable).add_columns("작업명", "설명", "최근상태")
        self.query_one("#batch_table", DataTable).cursor_type = "row"

        self._register_signal_handlers()
        self._cleanup_audit_task = asyncio.create_task(
            self._cleanup_audit_periodically(), name="audit-cleanup"
        )
        self.run_worker(self._cleanup_stale_run_files_worker, group="startup", exclusive=True)
        self.action_refresh()
        self.set_interval(3.0, self._refresh_server_status)

    def on_unmount(self) -> None:
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
            with contextlib.suppress(RuntimeError):
                t = asyncio.create_task(
                    audit(
                        action="tui_stop",
                        target="admin_tui",
                        outcome="unmount",
                        detail={"running": self._current_subprocess is not None},
                    )
                )
                self._background_tasks.add(t)
                t.add_done_callback(self._background_tasks.discard)
            self.run_worker(self._background_shutdown, group="shutdown", exclusive=True)

    async def _background_shutdown(self) -> None:
        try:
            async with self._operation_lock:
                await self._shutdown_orchestrator(detach_only=False)
        finally:
            await self._unregister_signal_handlers()

    # ── Option-as-Meta Detection ───────────────────────────────────────
    def _detect_option_as_meta(self) -> None:
        if sys.platform != "darwin":
            self._option_as_meta_detected = True
            return
        self._option_as_meta_detected = None

    def _maybe_update_option_as_meta(self, event) -> None:
        if self._option_as_meta_detected is None and sys.platform == "darwin":
            key = event.key
            if key.startswith("alt+") and not key.startswith("alt+shift+"):
                self._option_as_meta_detected = True
                self._hide_meta_warning_banner()
            elif event.character in (
                "¡",
                "™",
                "£",
                "¢",
                "∞",
                "§",
                "¶",
                "•",
                "ª",
                "º",
                "œ",
                "∑",
                "'",
                "®",
                "†",
                "¥",
                "ø",
                "π",
                "å",
                "ß",
                "∂",
                "ƒ",
                "©",
                "˙",
                "∆",
                "˚",
                "¬",
                "…",
                "Ω",
                "≈",
                "ç",
                "√",
                "∫",
                "µ",
                "≤",
                "≥",
                "÷",
            ):
                self._option_as_meta_detected = False
                self._show_meta_warning_banner()

    def _show_meta_warning_banner(self) -> None:
        banner = self.query_one("#meta_warning", Static)
        banner.update(
            "⚠️  macOS 터미널에서 Option 키가 Meta(Alt)로 동작하지 않습니다.  "
            "iTerm2: Preferences → Profiles → Keys → Left Option acts as: +Esc  "
            "Terminal.app: Preferences → Profiles → Keyboard → Use Option as Meta key  "
            "[Alt+X] 배너 숨기기  [F1] 도움말"
        )
        banner.styles.display = "block"

    def _hide_meta_warning_banner(self) -> None:
        self.query_one("#meta_warning", Static).styles.display = "none"

    # ── Centralized on_key filter ──────────────────────────────────────
    def on_key(self, event) -> None:
        # Phase 0: Option-as-Meta detection
        self._maybe_update_option_as_meta(event)

        key = event.key

        # 1. Emergency/safety: always pass through
        if key in (
            "escape",
            "shift+l",
            "q",
            "alt+q",
            "alt+u",
            "alt+x",
            "f1",
            "alt+shift+slash",
            "ctrl+question",
        ):
            return

        # 2. Edit modal (ScheduleEditScreen)
        if isinstance(self.screen, ScheduleEditScreen):
            # 2a. Block tab switch / cancel keys
            if key in (
                "ctrl+x",
                "ctrl+shift+x",
                "alt+1",
                "alt+2",
                "alt+3",
                "alt+4",
                "alt+5",
                "alt+6",
                "alt+7",
                "ctrl+1",
                "ctrl+2",
                "ctrl+3",
                "ctrl+4",
                "ctrl+5",
                "ctrl+6",
                "ctrl+7",
                "f2",
                "f3",
                "f4",
                "f5",
                "f6",
                "f7",
                "f8",
                "ctrl+tab",
                "ctrl+shift+tab",
            ):
                self.notify("지금은 편집 중입니다. Esc로 닫기", timeout=TOAST_SHORT_SECONDS)
                event.prevent_default()
                event.stop()
                return
            # 2b. Alt+S: edit modal save shortcut
            if key == "alt+s":
                return
            # 2c. Input/TextArea focus: pass all keys to widget
            if isinstance(self.screen.focused, (Input, TextArea)):
                return
            # 2d. Non-focus: only navigation passes
            if key not in ("tab", "shift+tab", "up", "down", "left", "right", "enter", "space"):
                self.notify("지금은 편집 중입니다. Esc로 닫기", timeout=TOAST_SHORT_SECONDS)
                event.prevent_default()
                event.stop()
            return

        # 3. Non-edit modal: allow navigation / quit, block rest
        if isinstance(self.screen, ModalScreen):
            if key in ("tab", "shift+tab", "up", "down", "left", "right", "enter", "space"):
                return
            if key in (
                "q",
                "alt+q",
                "escape",
                "f1",
                "alt+shift+slash",
                "ctrl+question",
            ):
                return
            event.prevent_default()
            event.stop()
            return

        # 4. Main screen: Input focus — pass most keys to Input widget
        if isinstance(self.screen.focused, Input):
            # Ctrl+X / Ctrl+Shift+X: job control, not search
            if key in ("ctrl+x", "ctrl+shift+x"):
                return
            # All other keys pass to Input widget
            return

        # Otherwise: allow action dispatch

    # ── Lock model ──────────────────────────────────────────────────────
    async def _is_cross_instance_lock_held(self) -> bool:
        if not LOCK_FILE.exists():
            return False
        fd = None
        try:
            fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
        except OSError:
            return False
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)

    async def _force_release_cross_instance_lock(self) -> bool:
        """cross-instance lock을 강제 해제한다.

        lock 파일이 없으면 할 일이 없으므로 False를 반환한다.
        lock이 다른 인스턴스에서 잡고 있으면 제한 시간 동안 비동기 폴링 후
        실패하면 False를 반환한다.
        """
        if not LOCK_FILE.exists():
            return False

        fd = None
        try:
            fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
            loop = asyncio.get_running_loop()
            start = loop.time()
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if loop.time() - start >= FORCE_RELEASE_LOCK_TIMEOUT:
                        await audit(
                            action="force_release_lock", target=str(LOCK_FILE), outcome="timeout"
                        )
                        return False
                    await asyncio.sleep(0.2)

            killed_any = await self._force_kill_all_batch_runners()

            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            await audit(
                action="force_release_lock",
                target=str(LOCK_FILE),
                outcome="manual",
                detail={"killed": killed_any},
            )
            return True
        except OSError:
            await audit(action="force_release_lock", target=str(LOCK_FILE), outcome="error")
            return False
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)

    async def _force_kill_all_batch_runners(self) -> bool:
        killed_any = False
        for pid_file in RUN_DIR.glob("batch_*.pid"):
            try:
                data = json.loads(pid_file.read_text(encoding="utf-8"))
                pid = int(data["pid"])
                pgid = int(data.get("pgid", pid))
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, OSError):
                    continue
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    killed_any = True
                except (ProcessLookupError, OSError):
                    pass
            except (ValueError, KeyError, FileNotFoundError, OSError):
                pass
        return killed_any

    # ── Signal handlers ────────────────────────────────────────────────
    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        self._registered_signals = [signal.SIGHUP, signal.SIGTERM, signal.SIGINT]
        self._original_signal_handlers = {}
        for sig in self._registered_signals:
            try:
                prev = loop.remove_signal_handler(sig)
            except (ValueError, NotImplementedError, OSError):
                prev = None
            self._original_signal_handlers[sig] = prev
            loop.add_signal_handler(sig, self._on_shutdown_signal)

    def _on_shutdown_signal(self) -> None:
        try:
            self.run_worker(self._show_signal_quit_dialog, group="shutdown", exclusive=True)
        except RuntimeError:
            with contextlib.suppress(RuntimeError):
                t = asyncio.create_task(self._emergency_shutdown())
                self._background_tasks.add(t)
                t.add_done_callback(self._background_tasks.discard)

    async def _emergency_shutdown(self) -> None:
        async with self._operation_lock:
            await self._shutdown_orchestrator(detach_only=False)
        self.exit()

    async def _show_signal_quit_dialog(self) -> None:
        current = self.screen
        if isinstance(current, ModalScreen) and not isinstance(current, ConfirmScreen):
            with contextlib.suppress(Exception):
                current.dismiss(None)
        if self._state is not None:
            msg = (
                f"작업 {self._state.action_name}이 실행 중입니다.\n"
                "TUI만 종료하면 작업은 백그라운드에서 계속됩니다. 어떻게 할까요?"
            )
            buttons = [
                ("종료(작업중단)", "shutdown"),
                ("TUI만 종료(작업계속)", "detach"),
                ("취소", False),
            ]
        else:
            msg = "종료 신호 수신. Admin TUI를 종료할까요?"
            buttons = [("종료", "shutdown"), ("취소", False)]
        choice = await self.push_screen_wait(ConfirmScreen(msg, buttons=buttons))
        if choice in ("shutdown", "detach"):
            async with self._operation_lock:
                await self._shutdown_orchestrator(detach_only=(choice == "detach"))
            self.exit()

    async def action_quit(self) -> None:
        async with self._operation_lock:
            if self._state is not None:
                msg = (
                    f"작업 {self._state.action_name}이 실행 중입니다.\n"
                    "TUI만 종료하면 작업은 백그라운드에서 계속됩니다. 어떻게 할까요?"
                )
                buttons = [
                    ("종료(작업중단)", "shutdown"),
                    ("TUI만 종료(작업계속)", "detach"),
                    ("취소", False),
                ]
            else:
                msg = "Admin TUI를 종료할까요?"
                buttons = [("종료", "shutdown"), ("취소", False)]
            choice = await self.push_screen_wait(ConfirmScreen(msg, buttons=buttons))
            if choice in ("shutdown", "detach"):
                await self._shutdown_orchestrator(detach_only=(choice == "detach"))
        if choice in ("shutdown", "detach"):
            self.exit()
        else:
            self.notify("종료 취소됨")

    async def _unregister_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in self._registered_signals:
            with contextlib.suppress(ValueError, NotImplementedError, OSError):
                loop.remove_signal_handler(sig)
            prev = self._original_signal_handlers.get(sig)
            if prev is not None:
                if prev == signal.SIG_DFL:
                    with contextlib.suppress(ValueError, OSError):
                        signal.signal(sig, signal.SIG_DFL)
                elif prev == signal.SIG_IGN:
                    with contextlib.suppress(ValueError, OSError):
                        signal.signal(sig, signal.SIG_IGN)
                elif callable(prev):
                    with contextlib.suppress(ValueError, NotImplementedError, OSError, TypeError):
                        loop.add_signal_handler(sig, prev)

    # ── Shutdown / detach ───────────────────────────────────────────────
    async def _shutdown_orchestrator(self, detach_only: bool = False) -> None:
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        if self._cancel_failure_auto_reset_task is not None:
            self._cancel_failure_auto_reset_task.cancel()
            self._cancel_failure_auto_reset_task = None
        await audit(
            action="tui_stop",
            target="admin_tui",
            outcome="detach" if detach_only else "shutdown",
            detail={"running": self._current_subprocess is not None},
        )
        key = self._state.key if self._state is not None else None
        proc = self._current_subprocess

        if self._state is not None:
            if detach_only:
                self._detaching = True
                self._detach_event.set()
                try:
                    await asyncio.wait_for(self._detach_complete_event.wait(), timeout=10.0)
                except TimeoutError:
                    logger.warning("detach cleanup timeout — forcing state clear")
                    tasks = [
                        t
                        for t in (self._reader_task, self._watcher_task, self._monitor_task)
                        if t is not None
                    ]
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if self._state is not None and self._state.key == key:
                        self._state = None
                        self._current_subprocess = None
                        self._reader_task = None
                        self._monitor_task = None
                        self._watcher_task = None
                return
            else:
                self._orchestrator_stop_event.set()
                try:
                    await asyncio.wait_for(self._cleanup_complete_event.wait(), timeout=35.0)
                except TimeoutError:
                    logger.warning("shutdown cleanup timeout — emergency finalize")
                    if proc is not None:
                        await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                    tasks = [
                        t
                        for t in (self._reader_task, self._monitor_task, self._watcher_task)
                        if t is not None
                    ]
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if key is not None:
                        await self._cleanup_after_process(key)
                return

        # idle state
        if detach_only:
            return

    # ── Subprocess execution + monitoring ───────────────────────────────
    async def _kill_process_group(
        self,
        pgid: int,
        use_sigterm_first: bool = True,
        sigterm_timeout: float = 10.0,
        sigkill_timeout: float = 5.0,
    ) -> bool:
        if use_sigterm_first:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                return True
            try:
                await asyncio.wait_for(self._process_exited_event.wait(), timeout=sigterm_timeout)
                return True
            except TimeoutError:
                pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            return True
        try:
            await asyncio.wait_for(self._process_exited_event.wait(), timeout=sigkill_timeout)
            return True
        except TimeoutError:
            return False

    async def _graceful_or_force_kill(self, proc, use_sigterm_first: bool = True) -> bool:
        if proc is None or proc.returncode is not None:
            return True
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return True
        if not self._killing:
            self._killing = True
        self._kill_attempts += 1
        if self._kill_attempts > KILL_ATTEMPT_LIMIT:
            logger.warning("kill 시도 횟수 초과 — 포기")
            return False
        try:
            return await self._kill_process_group(pgid, use_sigterm_first=use_sigterm_first)
        finally:
            pass

    async def _read_logs(self, log_path: Path, key: str) -> None:
        dropped = 0
        last_size = 0
        last_inode = None
        f = None
        try:
            while not self._shutdown_event.is_set() and not self._detach_event.is_set():
                path_exists = await asyncio.to_thread(log_path.exists)
                if not path_exists:
                    if f is not None:
                        await asyncio.to_thread(f.close)
                        f = None
                    await asyncio.sleep(0.5)
                    continue

                current_stat = await asyncio.to_thread(os.stat, log_path)
                current_size = current_stat.st_size
                current_inode = current_stat.st_ino

                inode_changed = last_inode is not None and current_inode != last_inode
                truncated = current_size < last_size
                if inode_changed or truncated or f is None:
                    if f is not None:
                        await asyncio.to_thread(f.close)
                    f = await asyncio.to_thread(builtins.open, log_path, "r")
                    last_size = 0
                    last_inode = current_inode

                if current_size > last_size:
                    await asyncio.to_thread(f.seek, last_size)
                    _f = f
                    lines = await asyncio.to_thread(
                        lambda f=_f: list(islice(f, LOG_READ_CHUNK_LINES))
                    )
                    for line in lines:
                        text = line.rstrip("\n")
                        try:
                            self._log_queue.put_nowait(text)
                        except asyncio.QueueFull:
                            dropped += 1
                            if self._state is not None and self._state.key == key:
                                self._state.log_drops = dropped
                            if dropped == 1 or dropped % 1000 == 0:
                                self.notify(f"로그 {dropped}행 드롭")
                    last_size = await asyncio.to_thread(f.tell)
                await asyncio.sleep(0.5)
            if self._state is not None and self._state.key == key:
                self._state.log_drops = dropped
        except Exception as exc:
            logger.warning(f"로그 tailing 오류: {exc}")
        finally:
            if f is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(f.close)

    async def _watch_heartbeat(self, key: str, proc) -> None:
        path = _heartbeat_path(key)
        timeout_seconds = BATCH_META.get(key, {}).get(
            "heartbeat_timeout_seconds", DEFAULT_HEARTBEAT_TIMEOUT
        )
        while True:
            await asyncio.sleep(2.0)
            if self._state is None or self._state.key != key:
                return
            if (
                self._process_exited_event.is_set()
                or self._detach_event.is_set()
                or self._killing
                or self._shutdown_event.is_set()
            ):
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, KeyError):
                continue
            if self._state is None or self._state.key != key:
                return
            self._state.progress = data.get("progress", self._state.progress)
            self._state.pct = data.get("pct", self._state.pct)
            self._state.last_heartbeat = datetime.fromisoformat(data["ts"])
            if utcnow() - self._state.last_heartbeat > timedelta(seconds=timeout_seconds):
                self.notify(
                    f"Heartbeat {timeout_seconds}초 초과 — 자동 종료", timeout=TOAST_LONG_SECONDS
                )
                await audit(
                    action="auto_kill",
                    target=key,
                    outcome="heartbeat_timeout",
                    detail={"timeout": timeout_seconds},
                )
                await self._graceful_or_force_kill(proc, use_sigterm_first=True)

    async def _monitor_process(self, key: str, proc) -> None:
        returncode = None
        log_drops = self._state.log_drops if (self._state and self._state.key == key) else 0
        try:
            returncode = await proc.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self._process_exited_event.set()
        if returncode is None:
            return
        self._last_returncode = returncode
        final_drops = (
            self._state.log_drops if (self._state and self._state.key == key) else log_drops
        )
        msg_detail = {"exit_code": returncode, "log_drops": final_drops}
        if returncode == 143:
            self.notify("작업이 중단되었습니다")
            await audit(action="cancel_batch", target=key, outcome="terminated", detail=msg_detail)
            self._last_results.appendleft(
                LastRunResult(
                    key=key, returncode=143, message="중단됨", ts=utcnow(), log_drops=final_drops
                )
            )
        elif returncode == 2:
            self.notify(
                "다른 프로세스가 lock을 점유 중입니다 — [Shift+L]로 강제해제 가능",
                timeout=TOAST_LONG_SECONDS,
            )
            await audit(action="run_batch", target=key, outcome="lock_busy", detail=msg_detail)
            self._last_results.appendleft(
                LastRunResult(
                    key=key, returncode=2, message="lock 점유", ts=utcnow(), log_drops=final_drops
                )
            )
        elif returncode != 0:
            self.notify(f"작업 비정상 종료 (code={returncode})", timeout=TOAST_LONG_SECONDS)
            await audit(action="run_batch", target=key, outcome="failed", detail=msg_detail)
            self._last_results.appendleft(
                LastRunResult(
                    key=key,
                    returncode=returncode,
                    message=f"비정상 종료 (code={returncode})",
                    ts=utcnow(),
                    log_drops=final_drops,
                )
            )
        else:
            self.notify("작업 완료")
            await audit(action="run_batch", target=key, outcome="succeeded", detail=msg_detail)
            self._last_results.appendleft(
                LastRunResult(
                    key=key, returncode=0, message="완료", ts=utcnow(), log_drops=final_drops
                )
            )

    async def _cleanup_after_process(self, key: str) -> None:
        for p in (_pid_path(key), _heartbeat_path(key)):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        if self._state is not None and self._state.key == key:
            self._killing = False
            self._kill_attempts = 0
            self._state = None
            self._current_subprocess = None
            self._reader_task = None
            self._monitor_task = None
            self._watcher_task = None
            self._process_exited_event.clear()
            self._cleanup_complete_event.set()
            self._detach_complete_event.set()
            self._refresh_all_tabs()

    async def _run_subprocess_batch(self, key: str) -> None:
        proc = None
        log_path = _log_path(key)

        # Phase 1: use _batch_lock only for concurrent prevention + fast shutdown/cancel
        async with self._batch_lock:
            if self._state is not None:
                self.notify("이미 실행 중인 작업")
                return
            if await self._is_cross_instance_lock_held():
                self.notify(
                    "다른 TUI/프로세스에서 작업 실행 중 — [Shift+L]로 강제해제 가능",
                    timeout=TOAST_LONG_SECONDS,
                )
                return
            if self._shutdown_event.is_set():
                return

            self._process_exited_event.clear()
            self._orchestrator_stop_event.clear()
            self._detach_event.clear()
            self._cleanup_complete_event.clear()
            self._detach_complete_event.clear()
            self._detaching = False
            self._killing = False
            self._kill_attempts = 0
            self._reader_task = None
            self._monitor_task = None
            self._watcher_task = None

            try:
                await audit(action="run_batch", target=key, outcome="started")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-u",
                    "-m",
                    "app.batch_runner",
                    key,
                    "--log",
                    str(log_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                lock_acquired = False
                lock_busy = False
                early_exit_code = None
                for _ in range(BATCH_LOCK_POLL_MAX_ATTEMPTS):
                    if self._shutdown_event.is_set():
                        await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                        self._killing = False
                        self._kill_attempts = 0
                        await audit(action="run_batch", target=key, outcome="shutdown_during_poll")
                        return
                    await asyncio.sleep(BATCH_LOCK_POLL_SECONDS)
                    if proc.returncode == 2:
                        lock_busy = True
                        break
                    if proc.returncode is not None:
                        early_exit_code = proc.returncode
                        break
                    if await self._is_cross_instance_lock_held():
                        lock_acquired = True
                        break
                if lock_busy:
                    self.notify(
                        "다른 프로세스가 lock을 점유 중입니다 — [Shift+L]로 강제해제 가능",
                        timeout=TOAST_LONG_SECONDS,
                    )
                    await audit(action="run_batch", target=key, outcome="lock_busy")
                    self._last_results.appendleft(
                        LastRunResult(
                            key=key, returncode=2, message="lock 점유", ts=utcnow(), log_drops=0
                        )
                    )
                    return
                if early_exit_code is not None:
                    if early_exit_code == 0:
                        await audit(
                            action="run_batch",
                            target=key,
                            outcome="succeeded",
                            detail={"exit_code": 0, "fast": True},
                        )
                        self.notify("작업이 빠르게 완료되었습니다")
                        self._last_results.appendleft(
                            LastRunResult(
                                key=key, returncode=0, message="빠른 완료", ts=utcnow(), log_drops=0
                            )
                        )
                    else:
                        await audit(
                            action="run_batch",
                            target=key,
                            outcome="early_exit",
                            detail={"exit_code": early_exit_code},
                        )
                        self.notify(
                            f"batch_runner가 초기에 종료되었습니다 (code={early_exit_code})",
                            timeout=TOAST_LONG_SECONDS,
                        )
                        self._last_results.appendleft(
                            LastRunResult(
                                key=key,
                                returncode=early_exit_code,
                                message=f"초기 종료 (code={early_exit_code})",
                                ts=utcnow(),
                                log_drops=0,
                            )
                        )
                    return
                if not lock_acquired:
                    self.notify(
                        "batch_runner가 lock을 획득하지 못했습니다", timeout=TOAST_LONG_SECONDS
                    )
                    await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                    await audit(action="run_batch", target=key, outcome="lock_acquire_failed")
                    self._last_results.appendleft(
                        LastRunResult(
                            key=key,
                            returncode=-1,
                            message="lock 획득 실패",
                            ts=utcnow(),
                            log_drops=0,
                        )
                    )
                    return
                if proc.returncode is not None:
                    await audit(
                        action="run_batch",
                        target=key,
                        outcome="early_exit",
                        detail={"exit_code": proc.returncode},
                    )
                    self.notify(
                        f"batch_runner가 lock 획득 직후 종료되었습니다 (code={proc.returncode})",
                        timeout=TOAST_LONG_SECONDS,
                    )
                    self._last_results.appendleft(
                        LastRunResult(
                            key=key,
                            returncode=proc.returncode,
                            message=f"lock 획득 직후 종료 (code={proc.returncode})",
                            ts=utcnow(),
                            log_drops=0,
                        )
                    )
                    return

                # Phase 2: acquire _operation_lock briefly before setting RunningState
                async with self._operation_lock:
                    if self._shutdown_event.is_set():
                        await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                        await audit(action="run_batch", target=key, outcome="shutdown_before_state")
                        return
                    if self._state is not None:
                        await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                        await audit(action="run_batch", target=key, outcome="concurrent_start_race")
                        return

                    self._state = RunningState(
                        key=key,
                        started_at=utcnow(),
                        action_name=key,
                        status="running",
                        progress="시작",
                        pct=0,
                        last_heartbeat=utcnow(),
                        log_drops=0,
                        log_path=log_path,
                    )
                    self._current_subprocess = proc
                _pid_path(key).write_text(
                    json.dumps(
                        {
                            "pid": proc.pid,
                            "pgid": os.getpgid(proc.pid),
                            "started_at": utcnow().isoformat(),
                            "log_path": str(log_path),
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning(f"배치 생성 오류: {exc}")
                self.notify(f"배치 생성 오류: {exc}", timeout=TOAST_LONG_SECONDS)
                await audit(
                    action="run_batch", target=key, outcome="error", detail={"error": str(exc)}
                )
                if proc is not None and proc.returncode is None:
                    await self._graceful_or_force_kill(proc, use_sigterm_first=False)
                return

        # Outside lock: start monitoring
        self._reader_task = asyncio.create_task(
            self._read_logs(log_path, key), name=f"reader-{key}"
        )
        self._monitor_task = asyncio.create_task(
            self._monitor_process(key, proc), name=f"monitor-{key}"
        )
        self._watcher_task = asyncio.create_task(
            self._watch_heartbeat(key, proc), name=f"heartbeat-{key}"
        )
        for t in (self._reader_task, self._monitor_task, self._watcher_task):
            t.add_done_callback(self._on_task_exception)

        try:
            await asyncio.wait_for(
                self._wait_for_process_or_stop_or_detach(),
                timeout=3600.0,
            )
        except TimeoutError:
            self.notify("배치 모니터링 타임아웃 — 수동 점검 필요", timeout=TOAST_LONG_SECONDS)
            await audit(action="monitor_timeout", target=key, outcome="fallback_timeout")

        if not self._detaching:
            if proc is not None:
                await self._graceful_or_force_kill(proc, use_sigterm_first=False)
            if self._monitor_task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(self._monitor_task, return_exceptions=True),
                        timeout=30.0,
                    )
                except TimeoutError:
                    self._monitor_task.cancel()
                    await asyncio.gather(self._monitor_task, return_exceptions=True)
            tasks = [t for t in (self._reader_task, self._watcher_task) if t is not None]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._state is not None and self._state.key == key:
                await self._cleanup_after_process(key)
        else:
            tasks = [
                t
                for t in (self._reader_task, self._watcher_task, self._monitor_task)
                if t is not None
            ]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._state is not None and self._state.key == key:
                self._state = None
                self._current_subprocess = None
                self._reader_task = None
                self._monitor_task = None
                self._watcher_task = None
                self._process_exited_event.clear()
                self._detach_complete_event.set()
                self.refresh_footer()
                self._refresh_all_tabs()

    async def _wait_for_process_or_stop_or_detach(self) -> None:
        exited_wait = asyncio.create_task(self._process_exited_event.wait())
        stop_wait = asyncio.create_task(self._orchestrator_stop_event.wait())
        detach_wait = asyncio.create_task(self._detach_event.wait())
        try:
            await asyncio.wait(
                {exited_wait, stop_wait, detach_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (exited_wait, stop_wait, detach_wait):
                t.cancel()
            await asyncio.gather(exited_wait, stop_wait, detach_wait, return_exceptions=True)

    def _on_task_exception(self, task: asyncio.Task) -> None:
        try:
            if not task.cancelled() and task.exception() is not None:
                logger.warning(f"Task crashed: {task.get_name()} — {task.exception()}")
                try:
                    t = asyncio.create_task(
                        audit(
                            action="task_crash",
                            target=task.get_name(),
                            outcome="error",
                            detail={"error": str(task.exception())},
                        )
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)
                except RuntimeError as exc:
                    logging.getLogger("admin_audit").warning(
                        "audit task 생성 실패(루프 종료 중): %s", exc
                    )
                self._orchestrator_stop_event.set()
        except Exception as exc:
            logging.getLogger("admin_tui").warning("Task exception handler 오류: %s", exc)

    # ── Cancel + auto recovery ──────────────────────────────────────────
    async def action_cancel_global(self) -> None:
        async with self._operation_lock:
            await self._trigger_cancel()

    async def action_force_release_global(self) -> None:
        async with self._operation_lock:
            await self._trigger_force_release()

    async def _trigger_cancel(self) -> None:
        proc = self._current_subprocess
        if self._state is None:
            self.notify("중단할 작업이 없습니다")
            return
        if self._state.status == "cancelling":
            self.notify("이미 중단 시도 중 — Ctrl+Shift+X로 강제해제", timeout=TOAST_LONG_SECONDS)
            return
        confirmed = await self.push_screen_wait(
            ConfirmScreen("실행 중인 작업을 중단할까요?", buttons=[("중단", True), ("취소", False)])
        )
        if not confirmed:
            self.notify("중단 취소됨")
            return
        self._state.status = "cancelling"
        self.notify("작업 중단 중...")
        ok = await self._graceful_or_force_kill(proc, use_sigterm_first=True)
        if not ok:
            self.notify(
                "작업이 응답하지 않습니다 — Ctrl+Shift+X로 강제해제, 또는 20초 후 자동 초기화",
                timeout=TOAST_LONG_SECONDS,
            )
            self._cancel_failure_auto_reset_task = asyncio.create_task(
                self._auto_reset_after_cancel_failure()
            )

    async def _auto_reset_after_cancel_failure(self) -> None:
        try:
            await asyncio.sleep(20.0)
            if self._state is not None and self._state.status == "cancelling":
                self.notify("20초 경과 — 상태를 자동 초기화합니다", timeout=TOAST_LONG_SECONDS)
                async with self._operation_lock:
                    await self._trigger_force_release()
        except asyncio.CancelledError:
            pass

    async def _trigger_force_release(self) -> None:
        current_task = asyncio.current_task()
        if (
            self._cancel_failure_auto_reset_task is not None
            and current_task is not self._cancel_failure_auto_reset_task
        ):
            self._cancel_failure_auto_reset_task.cancel()
            self._cancel_failure_auto_reset_task = None
        key = self._state.key if self._state is not None else None
        proc = self._current_subprocess
        if self._state is None or self._state.status != "cancelling":
            self.notify("강제해제는 취소 실패 상태에서만 사용할 수 있습니다")
            return
        confirmed = await self.push_screen_wait(
            ConfirmScreen(
                "작업을 강제로 종료하고 lock을 해제할까요?",
                buttons=[("강제해제", True), ("취소", False)],
            )
        )
        if not confirmed:
            self.notify("강제해제 취소됨")
            return
        self._orchestrator_stop_event.set()
        if proc is not None:
            await self._graceful_or_force_kill(proc, use_sigterm_first=False)
        tasks = [
            t for t in (self._reader_task, self._monitor_task, self._watcher_task) if t is not None
        ]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._force_release_cross_instance_lock()
        if self._state is not None and self._state.key == key:
            self._state = None
        self._killing = False
        self._kill_attempts = 0
        self._current_subprocess = None
        self._reader_task = None
        self._monitor_task = None
        self._watcher_task = None
        self.refresh_footer()
        self.notify("강제 해제 완료 — 상태 초기화됨")

    # ── Stale/orphan cleanup ───────────────────────────────────────────
    async def _cleanup_stale_run_files_worker(self) -> None:
        await self._cleanup_stale_run_files()

    async def _cleanup_stale_run_files(self) -> None:
        orphans = []
        for pid_file in RUN_DIR.glob("batch_*.pid"):
            key = pid_file.stem.replace("batch_", "")
            try:
                data = json.loads(pid_file.read_text(encoding="utf-8"))
                pid = int(data["pid"])
                pgid = int(data.get("pgid", pid))
                started_at = datetime.fromisoformat(data["started_at"])
                os.kill(pid, 0)
                if await self._verify_orphan(pid, pgid, started_at):
                    orphans.append((key, pid, pgid, pid_file))
                else:
                    pid_file.unlink(missing_ok=True)
                    _heartbeat_path(key).unlink(missing_ok=True)
            except (ValueError, KeyError, ProcessLookupError, OSError):
                pid_file.unlink(missing_ok=True)
                _heartbeat_path(key).unlink(missing_ok=True)

        if orphans:
            names = ", ".join(f"{key}(pid={pid})" for key, pid, _, _ in orphans)
            choice = await self.push_screen_wait(
                ConfirmScreen(
                    f"고아 프로세스 감지: {names}.\n종료하면 lock을 정리하고 새 작업을 시작할 수 있습니다.",
                    buttons=[("종료", "kill"), ("무시", "ignore")],
                )
            )
            if choice == "kill":
                for _key, _pid, pgid, _pid_file in orphans:
                    with contextlib.suppress(ProcessLookupError, OSError):
                        os.killpg(pgid, signal.SIGTERM)
                await asyncio.sleep(ORPHAN_SIGTERM_WAIT_SECONDS)
                for _key, _pid, pgid, _pid_file in orphans:
                    try:
                        os.kill(pid, 0)
                        with contextlib.suppress(ProcessLookupError, OSError):
                            os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    pid_file.unlink(missing_ok=True)
                    _heartbeat_path(key).unlink(missing_ok=True)
                await self._force_release_cross_instance_lock()
                self.notify("고아 프로세스 종료 및 lock 정리 완료")
            else:
                self.notify(
                    "고아 프로세스를 종료하지 않았습니다. lock이 해제될 때까지 새 작업은 시작할 수 없습니다. [Shift+L]강제해제",
                    timeout=TOAST_LONG_SECONDS,
                )

    async def _verify_orphan(self, pid: int, pgid: int, started_at: datetime) -> bool:
        try:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, OSError):
                return False
            current_pgid = os.getpgid(pid)
            if current_pgid != pgid:
                return False
            proc = await asyncio.create_subprocess_exec(
                "ps",
                "-o",
                "args=,etime=",
                "-p",
                str(pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await proc.communicate()
            if proc.returncode != 0:
                return False
            line = stdout.decode().strip()
            args, etime = line.rsplit(None, 1)
            if "batch_runner" not in args:
                return False
            elapsed_seconds = _parse_ps_etime(etime)
            expected_seconds = (utcnow() - started_at).total_seconds()
            return elapsed_seconds >= expected_seconds - 10
        except (ProcessLookupError, OSError, ValueError):
            return False

    # ── Audit periodic cleanup ─────────────────────────────────────────
    async def _cleanup_audit_periodically(self) -> None:
        while not self._shutdown_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=AUDIT_CLEANUP_INTERVAL_SECONDS
                )
            if self._shutdown_event.is_set():
                return
            try:
                await asyncio.to_thread(cleanup_audit)
            except Exception as exc:
                logger.warning("audit cleanup to_thread 실패(무시): %s", exc)

    # ── Tab switching ───────────────────────────────────────────────────
    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_next_tab(self) -> None:
        tabs = self.query_one(TabbedContent)
        ids = [p.id for p in tabs.query(TabPane) if p.id]
        if not ids:
            return
        current = tabs.active
        try:
            idx = ids.index(current)
        except ValueError:
            idx = -1
        next_idx = (idx + 1) % len(ids)
        tabs.active = ids[next_idx]

    def action_prev_tab(self) -> None:
        tabs = self.query_one(TabbedContent)
        ids = [p.id for p in tabs.query(TabPane) if p.id]
        if not ids:
            return
        current = tabs.active
        try:
            idx = ids.index(current)
        except ValueError:
            idx = 0
        prev_idx = (idx - 1) % len(ids)
        tabs.active = ids[prev_idx]

    # ── Refresh ─────────────────────────────────────────────────────────
    def action_refresh(self) -> None:
        db = SessionLocal()
        try:
            counts = admin_status.table_counts(db)
            fresh = admin_status.freshness(db)
        finally:
            db.close()
        lines = [
            "[b]시스템 상태[/b]  (Alt+1~7=탭, r=새로고침, q=종료)",
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
        self._load_preview()
        self._load_batch_table()
        self._load_results_table()
        self._load_lock_status()
        self._load_progress()
        self._load_release_info()

    def _refresh_all_tabs(self) -> None:
        self.action_refresh()

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
        with contextlib.suppress(Exception):
            self.query_one("#server_status", Static).update("\n".join(lines))

    @staticmethod
    def _login_gate_line() -> str:
        enabled = web_login_enabled()
        if enabled is None:
            return "웹 로그인  [dim]? web/.env.local 없음[/dim]"
        if enabled:
            return "웹 로그인  [green]● 켜짐[/green] (LOGIN_PASSWORD 설정됨)"
        return "웹 로그인  [yellow]○ 꺼짐[/yellow] (LOGIN_PASSWORD 미설정 — 게이트 열림)"

    def _check_health(self) -> None:
        """API/WEB health check (h/Alt+H 단축키 → 버튼)."""
        self._log_line("▶ health check…")
        try:
            api = self._servers.health("api")
            web = self._servers.health("web")
            api_ok = api.get("ok", False)
            web_ok = web.get("ok", False)
            msg = (
                f"API: {'✓ OK' if api_ok else '✗ FAIL'} | "
                f"WEB: {'✓ OK' if web_ok else '✗ FAIL'}"
            )
        except Exception as e:
            msg = f"✗ health check 실패: {e}"
        self._log_line(msg)

    def _load_lock_status(self) -> None:
        async def _update():
            held = await self._is_cross_instance_lock_held()
            text = "🔒 batch_runner가 lock 점유 중" if held else "🔓 lock 해제됨"
            with contextlib.suppress(Exception):
                self.query_one("#lock_status", Static).update(
                    f"{text}   마지막 갱신: {datetime.now():%H:%M:%S}"
                )

        self.run_worker(_update(), group="lock-status", exclusive=True)

    def _load_progress(self) -> None:
        if self._state is not None:
            bar_len = 20
            filled = int(self._state.pct / 100 * bar_len) if self._state.pct < 100 else bar_len
            bar = "█" * filled + "░" * (bar_len - filled)
            text = f"진행률: {self._state.pct}% {bar}  단계: {self._state.progress}"
        else:
            text = "[실행 중인 작업 없음]"
        with contextlib.suppress(Exception):
            self.query_one("#progress_bar", Static).update(text)

    def _load_results_table(self) -> None:
        table = self.query_one("#results_table", DataTable)
        table.clear()
        for r in list(self._last_results)[:5]:
            ts = r.ts.strftime("%H:%M:%S")
            table.add_row(ts, r.key, r.message, str(r.returncode), str(r.log_drops))

    def _load_batch_table(self) -> None:
        table = self.query_one("#batch_table", DataTable)
        table.clear()
        # worker 가 ingest_log 에 남긴 각 잡의 최신 결과를 한 번에 조회. 메모리 deque(_last_results)
        # 만 보면 스케줄 실행 결과가 TUI 재시작마다 사라져 '최근상태'가 항상 비어 보인다.
        log_jobs = [BATCH_KEY_TO_LOG_JOB.get(key, key) for key, _ in MANUAL_BATCHES]
        db = SessionLocal()
        try:
            latest = ingest_log.latest_for_jobs(db, log_jobs)
        finally:
            db.close()
        for key, label in MANUAL_BATCHES:
            meta = BATCH_META.get(key, {})
            desc = meta.get("label", label)
            log_job = BATCH_KEY_TO_LOG_JOB.get(key, key)
            status = self._fmt_batch_status(key, log_job, latest)
            table.add_row(label, desc, status)
        table.add_row("릴리스 배포", "release_deploy", "-")

    def _fmt_batch_status(self, key: str, log_job: str, latest: dict) -> str:
        """배치 최근상태 셀 텍스트. TUI 세션 수행이 더 최근이면 그것, 아니면 DB 최신 행."""
        last = next((r for r in self._last_results if r.key == key), None)
        db_row = latest.get(log_job)
        if last is not None and (db_row is None or last.ts >= db_row.ts):
            mark = "[green]✔[/green]" if last.returncode == 0 else "[red]✖[/red]"
            return f"{mark} {last.message}"
        if db_row is None:
            return "-"
        ts = db_row.ts.astimezone().strftime("%m-%d %H:%M")
        detail = (db_row.detail or "")[:36]
        if db_row.status == "ok":
            return f"[green]✔[/green] {ts} {detail}"
        return f"[red]✖[/red] {ts} {detail}"

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
            table.add_row(
                job.suffix,
                job.time_label,
                job.desc,
                job.desc[:20],
                state,
            )
        if self._jobs_cache:
            table.move_cursor(row=min(prev_row, len(self._jobs_cache) - 1))

    def _load_db_status(self) -> None:
        db = SessionLocal()
        try:
            statuses = admin_status.db_status(db)
            backfills = admin_status.all_backfill_progress(db)
        finally:
            db.close()
        lines = ["[b]DB 적재 현황[/b]  (최신순)"]
        for b in backfills:
            pct_str = f"{b.pct:.1f}%" if b.pct < 100 else "[green]100% ✔[/green]"
            bar_len = 20
            filled = int(b.pct / 100 * bar_len) if b.pct < 100 else bar_len
            bar = "█" * filled + "░" * (bar_len - filled)
            if b.remaining > 0:
                est_days = b.remaining / b.per_run
                est = (
                    f"  ~{est_days:.0f}일 후" if est_days >= 1 else f"  ~{est_days * 24:.0f}시간 후"
                )
            else:
                est = ""
            detail = f"  [dim]{b.detail}[/dim]" if b.detail else ""
            lines.append(
                f"  {b.label:12s} {bar} {pct_str:>8s}  {b.done:,}/{b.total:,}{est}{detail}"
            )
        self.query_one("#db_title", Static).update("\n".join(lines))
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

    def _load_preview(self) -> None:
        sort = self._sort_keys[self._sort_idx]
        db = SessionLocal()
        try:
            page = admin_status.screener_preview(
                db, sort=sort, limit=_PREVIEW_LIMIT, offset=self._page * _PREVIEW_LIMIT
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
        total_pages = max(1, -(-self._total // _PREVIEW_LIMIT))
        start = self._page * _PREVIEW_LIMIT + 1 if page.rows else 0
        end = start + len(page.rows) - 1 if page.rows else 0
        self.query_one("#preview_info", Static).update(
            f"[b]스몰캡 성장주[/b]  {start}-{end} / {self._total}  "
            f"(페이지 {self._page + 1}/{total_pages}, 정렬: {sort})"
        )
        self.query_one("#prev", Button).disabled = self._page <= 0
        self.query_one("#next", Button).disabled = (self._page + 1) >= total_pages

    def _load_release_info(self) -> None:
        try:
            git = sc.git_info()
            deploy = sc.last_deploy_info()
            lines = []
            if git.get("branch"):
                lines.append(f"현재 브랜치: {git['branch']}  커밋: {git.get('commit', '—')}")
                ahead = git.get("ahead", 0)
                behind = git.get("behind", 0)
                lines.append(f"origin/main 대비: +{ahead}/-{behind}")
            if deploy.get("tag"):
                lines.append(f"마지막 배포: {deploy['tag']} @ {deploy.get('ts', '—')}")
            if deploy.get("branch"):
                lines.append(f"롤백 대상: {deploy['branch']}")
            # Health checks
            api_health = self._servers.health("api")
            web_health = self._servers.health("web")
            api_mark = "✓" if api_health.get("ok") else "✗"
            web_mark = "✓" if web_health.get("ok") else "✗"
            lines.append(f"health: API {api_mark}  WEB {web_mark}")
            self.query_one("#release_info", Static).update("\n".join(lines))
        except Exception as exc:
            self.query_one("#release_info", Static).update(
                f"[dim]릴리스 정보 로드 실패: {exc}[/dim]"
            )

    # ── Schedule actions ──────────────────────────────────────────────
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

        def _apply(value: object) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                suffix = value.get("suffix", job.suffix)
                h, m = value["hour"], value["minute"]
                enabled = value.get("enabled", job.enabled)
                if enabled != job.enabled:
                    self._log_line(self._schedule.toggle(suffix, job.enabled))
                self._log_line(self._schedule.set_time(suffix, h, m))
                self._load_schedule()

        self.push_screen(
            ScheduleEditScreen(
                job.suffix,
                job.desc,
                job.hour,
                job.minute,
                job.enabled,
            ),
            _apply,
        )

    def action_new_job(self) -> None:
        def _apply(value: object) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                self._log_line(
                    f"신규 추가 요청: {value.get('suffix', '?')} — launchd/install.sh로 등록하세요."
                )
                self._load_schedule()

        self.push_screen(
            ScheduleEditScreen(
                "new_job",
                "신규 발송 잡",
                8,
                0,
                True,
                is_new=True,
            ),
            _apply,
        )

    # ── Sort / page ─────────────────────────────────────────────────────
    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self._sort_keys)
        self._page = 0
        self._load_preview()

    def action_next_page(self) -> None:
        total_pages = max(1, -(-self._total // _PREVIEW_LIMIT))
        if self._page + 1 < total_pages:
            self._page += 1
            self._load_preview()

    def action_prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._load_preview()

    # ── Stock search ────────────────────────────────────────────────────
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
        except Exception as e:
            text = f"[red]검색 실패: {e}[/red]"
        finally:
            db.close()
        self.call_from_thread(self.query_one("#detail", Static).update, text)

    def _format_stock_detail(self, db, hits: list, q: str) -> str:
        if not hits:
            return f"'{q}' 검색 결과 없음."
        if len(hits) > 1:
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

    # ── Button handlers ─────────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        # ── 탭 전환 버튼 ──
        if bid.startswith("tab_btn_"):
            tab = bid.replace("tab_btn_", "tab_")
            self.action_show_tab(tab)
            return
        # ── 전역 작업 버튼 ──
        if bid == "btn_refresh":
            self.action_refresh()
        elif bid == "btn_search":
            tabs = self.query_one(TabbedContent)
            if tabs.active == "tab_batch":
                self.query_one("#batch_search", Input).focus()
            elif tabs.active == "tab_ingest":
                self.query_one("#ingest_filter", Input).focus()
            elif tabs.active == "tab_stocks":
                self.query_one("#search_input", Input).focus()
        elif bid == "btn_cancel":
            self.action_cancel_global()
        elif bid in ("btn_force_cancel", "btn_lock_release"):
            self.action_force_release_global()
        elif bid == "btn_help":
            self.push_screen(HelpScreen())
        # ── 기존 버튼들 ──
        elif bid in ("api_restart", "web_restart"):
            key = bid.split("_")[0]
            self._log_line(self._servers.restart(key))
            self._refresh_server_status()
        elif bid == "web_build":
            self._run_web_build()
        elif bid == "prod_deploy":
            self._confirm_prod_deploy()
        elif bid == "prod_rollback":
            self._confirm_prod_rollback()
        elif bid == "sort":
            self.action_cycle_sort()
        elif bid == "next":
            self.action_next_page()
        elif bid == "prev":
            self.action_prev_page()
        # ── 스케줄 탭 버튼 ──
        elif bid == "btn_edit_job":
            self.action_edit_job()
        elif bid == "btn_new_job":
            self.action_new_job()
        # ── 모니터링 탭 버튼 ──
        elif bid == "log_health":
            self._check_health()
        # ── 로그레벨 토글 ──
        elif bid.startswith("log_"):
            level = bid.split("_", 1)[1]
            if level != "health":
                self._toggle_log_level(level)

    # ── WEB build ───────────────────────────────────────────────────────
    @work(thread=True, exclusive=True, group="busy")
    def _run_web_build(self) -> None:
        self.call_from_thread(self._log_line, "▶ WEB 빌드 시작… (수십 초 걸립니다)")
        try:
            msg = self._servers.build_web()
        except Exception as e:
            msg = f"✖ WEB 빌드 실패: {e}"
        self.call_from_thread(self._log_line, msg)

    # ── Deploy / Rollback ───────────────────────────────────────────────
    def _confirm_prod_deploy(self) -> None:
        async def _on_confirm(value: object) -> None:
            if value == "deploy":
                self._run_prod_deploy()

        self.push_screen(
            ConfirmScreen(
                "release 배포를 진행할까요?\n\nmain 의 커밋을 release 로 push 해 프로덕션 CD(자동 배포)를 트리거합니다.\n라이브 서비스에 반영됩니다.",
                buttons=[("배포", "deploy"), ("취소", False)],
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
        if "트리거됨" in msg:
            self._poll_cd()

    def _confirm_prod_rollback(self) -> None:
        async def _on_confirm(value: object) -> None:
            if value == "rollback":
                self._run_prod_rollback()

        self.push_screen(
            ConfirmScreen(
                "마지막 배포 태그로 롤백할까요?\n\nrelease 브랜치를 직전 커밋으로 강제 이동해 CD를 다시 트리거합니다.",
                buttons=[("롤백", "rollback"), ("취소", False)],
            ),
            _on_confirm,
        )

    @work(thread=True, exclusive=True, group="prod")
    def _run_prod_rollback(self) -> None:
        self.call_from_thread(self._log_line, "▶ release 롤백 중…")
        try:
            msg = self._prod.rollback()
        except Exception as e:
            msg = f"✖ release 롤백 실패: {e}"
        self.call_from_thread(self._log_line, msg)

    def _poll_cd(self) -> None:
        for _ in range(20):
            time.sleep(15)
            try:
                status = self._prod.cd_status()
            except Exception:
                break
            self.call_from_thread(self._log_line, status)
            if "[진행중" not in status:
                break

    # ── Log level toggle ────────────────────────────────────────────────
    def _toggle_log_level(self, level: str) -> None:
        if level in self._log_levels:
            self._log_levels[level] = not self._log_levels[level]
            btn = self.query_one(f"#log_{level}", Button)
            if self._log_levels[level]:
                btn.variant = (
                    "primary"
                    if level == "info"
                    else "warning"
                    if level == "warn"
                    else "error"
                    if level == "error"
                    else "default"
                )
            else:
                btn.variant = "default"
            self._log_line(f"로그 레벨 {level}: {'켜짐' if self._log_levels[level] else '꺼짐'}")

    # ── Batch table row selection ───────────────────────────────────────
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        if table_id == "batch_table":
            row_key = event.cursor_row
            if row_key is not None and row_key < len(MANUAL_BATCHES):
                key = MANUAL_BATCHES[row_key][0]
                self._confirm_run_batch(key)
            elif row_key == len(MANUAL_BATCHES):
                self._confirm_run_batch("release_deploy")

    def _confirm_run_batch(self, key: str) -> None:
        async def _on_confirm(value: object) -> None:
            if value == "run":
                self.run_worker(self._run_subprocess_batch(key), group="batch", exclusive=True)

        meta = BATCH_META.get(key, {})
        label = meta.get("label", key)
        self.push_screen(
            ConfirmScreen(
                f"'{label}' 작업을 실행할까요?\n\n실제 크롤/LLM/배포를 수행합니다.",
                buttons=[("실행", "run"), ("취소", False)],
            ),
            _on_confirm,
        )

    # ── Log line helper ─────────────────────────────────────────────────
    def _log_line(self, msg: str) -> None:
        self.query_one("#log", Log).write_line(f"[{datetime.now():%H:%M:%S}] {msg}")


def main() -> None:
    AdminTUI().run()


if __name__ == "__main__":
    main()
