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
from app.services import admin_status, growth_ingest, ingest, universe_ingest


class _LogHandler(logging.Handler):
    """서비스 로거 → TUI Log 위젯으로 흘려보낸다."""

    def __init__(self, log_widget: Log):
        super().__init__(level=logging.INFO)
        self._log = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # 위젯 파괴 등은 무시
            self._log.write_line(self.format(record))


class AdminTUI(App):
    TITLE = "reporter admin"
    CSS = """
    #status { height: auto; border: round $accent; padding: 1; }
    #actions { height: auto; padding: 1; }
    #actions Button { margin: 0 1; }
    #log { height: 1fr; border: round $secondary; }
    #preview { height: 15; border: round $primary; }
    .running { color: $warning; }
    """
    BINDINGS: ClassVar = [
        ("r", "refresh", "상태 새로고침"),
        ("q", "quit", "종료"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static(id="status")
            with Horizontal(id="actions"):
                yield Button("리포트 수집", id="ingest", variant="primary")
                yield Button("유니버스 스냅샷", id="universe", variant="primary")
                yield Button("성장 배치", id="growth", variant="primary")
                yield Button("새로고침", id="refresh")
            yield Log(id="log", highlight=True)
            yield DataTable(id="preview")
        yield Footer()

    def on_mount(self) -> None:
        init_db()
        # 서비스 로그를 TUI 로그 패널로 라우팅
        handler = _LogHandler(self.query_one("#log", Log))
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s", "%H:%M:%S"))
        logging.getLogger("app").addHandler(handler)
        logging.getLogger("app").setLevel(logging.INFO)
        logging.getLogger("reporter").addHandler(handler)
        logging.getLogger("reporter").setLevel(logging.INFO)

        table = self.query_one("#preview", DataTable)
        table.add_columns("스몰캡 성장주 (매출YoY순)", "시총(억)", "매출YoY", "모멘텀")
        self.action_refresh()

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
        self._load_preview()

    def _load_preview(self) -> None:
        """스몰캡 성장주 상위(매출 YoY) 미리보기."""
        db = SessionLocal()
        try:
            rows = admin_status.screener_preview(db, limit=10)
        finally:
            db.close()
        table = self.query_one("#preview", DataTable)
        table.clear()
        for r in rows:
            cap = f"{r.market_cap / 1e8:,.0f}" if r.market_cap else "—"
            ry = f"{r.revenue_yoy * 100:+.0f}%" if r.revenue_yoy is not None else "—"
            mm = f"{r.momentum_3m:+.0f}%" if r.momentum_3m is not None else "—"
            table.add_row(r.stock_name, cap, ry, mm)

    # --- 액션 (워커 스레드) ---
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "refresh":
            self.action_refresh()
        elif bid == "ingest":
            self._run_ingest()
        elif bid == "universe":
            self._run_universe()
        elif bid == "growth":
            self._run_growth()

    def _log_line(self, msg: str) -> None:
        self.query_one("#log", Log).write_line(f"[{datetime.now():%H:%M:%S}] {msg}")

    @work(thread=True, exclusive=True, group="job")
    def _run_ingest(self) -> None:
        self.call_from_thread(self._log_line, "▶ 리포트 수집 시작…")
        db = SessionLocal()
        try:
            settings = get_settings()
            n = ingest.ingest_reports(db, settings)
            ingest.build_market_brief(db, settings)
            self.call_from_thread(self._log_line, f"✔ 리포트 수집 완료: 신규 {n}건")
        except Exception as e:
            self.call_from_thread(self._log_line, f"✖ 리포트 수집 실패: {e}")
        finally:
            db.close()
        self.call_from_thread(self.action_refresh)

    @work(thread=True, exclusive=True, group="job")
    def _run_universe(self) -> None:
        self.call_from_thread(self._log_line, "▶ 유니버스 스냅샷 시작…")
        db = SessionLocal()
        try:
            n = universe_ingest.snapshot_universe(db, datetime.now().date())
            self.call_from_thread(self._log_line, f"✔ 유니버스 스냅샷 완료: {n}종목")
        except Exception as e:
            self.call_from_thread(self._log_line, f"✖ 유니버스 스냅샷 실패: {e}")
        finally:
            db.close()
        self.call_from_thread(self.action_refresh)

    @work(thread=True, exclusive=True, group="job")
    def _run_growth(self) -> None:
        self.call_from_thread(self._log_line, "▶ 성장 배치 시작… (수 분 소요)")
        db = SessionLocal()
        try:
            result = growth_ingest.run_growth_batch(db)
            self.call_from_thread(self._log_line, f"✔ 성장 배치 완료: {result}")
        except Exception as e:
            self.call_from_thread(self._log_line, f"✖ 성장 배치 실패: {e}")
        finally:
            db.close()
        self.call_from_thread(self.action_refresh)


def main() -> None:
    AdminTUI().run()


if __name__ == "__main__":
    main()
