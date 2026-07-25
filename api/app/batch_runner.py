"""Admin TUI 배치 서브프로세스.

`python -m app.batch_runner <key> --log <path>`로 실행된다.
- fcntl 기반 cross-instance lock(operation.lock)을 획득한다.
- heartbeat 파일을 2초 간격으로 갱신한다.
- stdout/stderr를 지정된 로그 파일로 리다이렉트한다.
- SIGTERM 수신 시 exit 143을 반환한다.
- lock 획득 실패 시 exit 2를 반환한다.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.admin_paths import HEARTBEAT_FILE_TEMPLATE, LOCK_FILE, PID_FILE_TEMPLATE, RUN_DIR
from app.config import get_settings
from app.scheduler import BATCH_FUNCTIONS, BATCH_META

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 2.0


class _RunnerState:
    def __init__(self, key: str) -> None:
        self.key = key
        self.cancelled = False
        self.returncode = 0
        self.lock_fd: int | None = None
        self.progress = "시작"
        self.pct = 0
        self._heartbeat_path = RUN_DIR / HEARTBEAT_FILE_TEMPLATE.format(key=key)
        self._pid_path = RUN_DIR / PID_FILE_TEMPLATE.format(key=key)
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def start(self, log_path: Path) -> None:
        self._write_pid(log_path)
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _write_pid(self, log_path: Path) -> None:
        try:
            self._pid_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "pgid": os.getpgid(os.getpid()),
                        "started_at": _utcnow().isoformat(),
                        "log_path": str(log_path),
                    }
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("pid write failed: %s", exc)

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self._write_heartbeat()
            self._stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS)
        self._write_heartbeat()

    def _write_heartbeat(self) -> None:
        try:
            self._heartbeat_path.write_text(
                json.dumps(
                    {
                        "progress": self.progress,
                        "pct": self.pct,
                        "ts": _utcnow().isoformat(),
                    }
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("heartbeat write failed: %s", exc)

    def update(self, progress: str, pct: int | None = None) -> None:
        self.progress = progress
        if pct is not None:
            self.pct = pct
        self._write_heartbeat()

    def stop(self) -> None:
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5.0)

    def cleanup(self) -> None:
        self.stop()
        for p in (self._heartbeat_path, self._pid_path):
            with contextlib.suppress(Exception):
                p.unlink(missing_ok=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _acquire_lock() -> int:
    """operation.lock을 non-blocking으로 획득. 실패 시 exit 2."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        sys.exit(2)
    return fd


def _release_lock(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


def _redirect_to_log(log_path: Path) -> None:
    """stdout/stderr를 로그 파일로 리다이렉트한다."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Append 모드로 열어서, TUI가 rotate 하더라도 동일 fd를 유지하지만
    # TUI는 inode 변경을 감지해 새 파일을 읽는다.
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(fd, sys.stdout.fileno())
    os.dup2(fd, sys.stderr.fileno())
    os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("key")
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    log_path = Path(args.log)
    key = args.key

    # SIGPIPE 무시 — 파이프가 끊겨도 프로세스가 죽지 않는다.
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    # 로그 리다이렉트는 lock 획득 전에 수행해 lock 메시지도 로그에 남긴다.
    _redirect_to_log(log_path)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    logger.info("batch_runner start: key=%s pid=%s", key, os.getpid())

    lock_fd = _acquire_lock()
    state = _RunnerState(key)

    def _on_sigterm(signum, frame) -> None:
        logger.info("SIGTERM received, cancelling")
        state.cancelled = True
        state.returncode = 143
        state.update("중단 요청 수신", 0)
        sys.exit(143)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        state.start(log_path)
        fn = BATCH_FUNCTIONS.get(key)
        if fn is None:
            raise ValueError(f"unknown batch key: {key}")

        meta = BATCH_META.get(key, {})
        state.update(meta.get("label", key), 0)

        settings = get_settings()
        result = fn(settings)
        state.update("완료", 100)
        logger.info("batch_runner completed: %s", result)
    except SystemExit as exc:
        state.returncode = exc.code if isinstance(exc.code, int) else 1
        raise
    except Exception as exc:
        state.returncode = 1
        state.update(f"오류: {exc}", 0)
        logger.exception("batch_runner failed")
    finally:
        _release_lock(lock_fd)
        state.cleanup()

    return state.returncode


if __name__ == "__main__":
    sys.exit(main())
