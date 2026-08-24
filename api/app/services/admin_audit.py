"""Admin TUI audit — SQLite WAL 기반 관리 이력 기록.

TUI 내 작업 실행, 취소, lock 강제 해제, 종료 등을 별도 SQLite DB에 기록한다.
기록 실패는 작업 흐름을 중단하지 않으며 로그만 남긴다.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import logging
import sqlite3
from datetime import UTC, timedelta

from app.admin_paths import AUDIT_DB

logger = logging.getLogger("admin_audit")

_AUDIT_RETENTION_DAYS = 30


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit (
            ts TEXT,
            action TEXT,
            target TEXT,
            outcome TEXT,
            detail TEXT,
            user TEXT
        )
        """
    )


def _write(
    action: str,
    target: str,
    outcome: str,
    detail: dict | None = None,
) -> None:
    """동기 SQLite 쓰기. 실패 시 예외를 던지지 않고 로그만 남긴다."""
    conn = None
    try:
        AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(AUDIT_DB, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?)",
            (
                _utcnow().isoformat(),
                action,
                target,
                outcome,
                json.dumps(detail or {}),
                getpass.getuser(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("audit write failed: %s", exc)
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


async def audit(
    action: str,
    target: str,
    outcome: str,
    detail: dict | None = None,
) -> None:
    """비동기 wrapper — to_thread로 SQLite에 기록한다."""
    try:
        await __import__("asyncio").to_thread(_write, action, target, outcome, detail)
    except Exception as exc:
        logger.warning("audit to_thread failed: %s", exc)


def cleanup_audit(retention_days: int = _AUDIT_RETENTION_DAYS) -> None:
    """retention_days 초과 audit 레코드를 삭제하고 PRAGMA optimize를 실행한다."""
    conn = None
    try:
        if not AUDIT_DB.exists():
            return
        conn = sqlite3.connect(AUDIT_DB, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()
        conn.execute("DELETE FROM audit WHERE ts < ?", (cutoff,))
        conn.execute("PRAGMA optimize")
        conn.commit()
    except Exception as exc:
        logger.warning("audit cleanup failed: %s", exc)
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def recent_audits(limit: int = 50) -> list[dict]:
    """최근 audit 레코드를 조회한다."""
    if not AUDIT_DB.exists():
        return []
    conn = None
    try:
        conn = sqlite3.connect(AUDIT_DB, timeout=10.0)
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT ts, action, target, outcome, detail, user FROM audit ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "ts": ts,
                "action": action,
                "target": target,
                "outcome": outcome,
                "detail": json.loads(detail) if detail else {},
                "user": user,
            }
            for ts, action, target, outcome, detail, user in rows
        ]
    except Exception as exc:
        logger.warning("recent audits read failed: %s", exc)
        return []
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _utcnow():
    from datetime import datetime

    return datetime.now(UTC)
