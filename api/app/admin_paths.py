"""Admin TUI / batch_runner 공용 경로 및 상수."""

from __future__ import annotations

import os
from pathlib import Path

RUN_DIR = Path(os.getenv("REPORTER_RUN_DIR", "~/.local/share/reporter")).expanduser()
LOCK_FILE = RUN_DIR / "operation.lock"
PID_FILE_TEMPLATE = "batch_{key}.pid"
HEARTBEAT_FILE_TEMPLATE = "batch_{key}.heartbeat"
LOG_DIR = RUN_DIR / "Logs"
AUDIT_DB = RUN_DIR / "admin_audit.db"
LAST_DEPLOY_TAG_FILE = RUN_DIR / "last_deploy_tag.txt"
LAST_DEPLOY_BRANCH_FILE = RUN_DIR / "last_deploy_branch.txt"

RUN_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
