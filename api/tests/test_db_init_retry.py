"""init_db 잠금 경합 재시도 로직 단위 테스트 — 실제 DB 없이 engine 을 모킹한다."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.db import session as db_session


def _lock_err() -> OperationalError:
    return OperationalError("SELECT pg_advisory_xact_lock", {}, Exception("55P03 lock_not_available"))


def test_init_db_retries_then_succeeds():
    """잠금 경합 시 상한 내에서 재시도하고, 풀리면 마이그레이션을 완료한다(#779)."""
    conn = MagicMock()
    stmts: list[str] = []
    attempts = {"n": 0}

    def exec_effect(stmt, *args, **kwargs):
        s = str(stmt)
        stmts.append(s)
        if "pg_advisory_xact_lock" in s:  # advisory lock 첫 획득만 경합 실패
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _lock_err()
        return None

    conn.execute.side_effect = exec_effect
    engine = MagicMock()
    engine.begin.return_value.__enter__ = MagicMock(return_value=conn)
    engine.begin.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(db_session, "engine", engine),
        patch.object(db_session.Base.metadata, "create_all"),
        patch.object(db_session.time, "sleep") as sleep,
        patch.object(db_session.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        db_session.init_db()

    assert sleep.called  # 재시도 전 대기
    # 첫 실패 이후 재시도에서 advisory lock 을 다시 시도했다.
    assert sum("pg_advisory_xact_lock" in s for s in stmts) == 2


def test_init_db_gives_up_after_deadline():
    """상한(300초) 초과까지 잠금 못 얻으면 포기하고 raise — 무한 대기 금지."""
    conn = MagicMock()
    conn.execute.side_effect = _lock_err()
    engine = MagicMock()
    engine.begin.return_value.__enter__ = MagicMock(return_value=conn)
    engine.begin.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(db_session, "engine", engine),
        patch.object(db_session.Base.metadata, "create_all"),
        patch.object(db_session.time, "sleep"),
        patch.object(db_session.time, "monotonic", side_effect=[0.0, 999.0]),
        pytest.raises(OperationalError),
    ):
        db_session.init_db()
