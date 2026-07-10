"""timeline 회귀 테스트 — DART 동기화 실패 시 세션 롤백 후 정상 반환(500 방지).

sync_disclosures 가 예외를 던져 세션이 오염돼도, company_timeline 이 rollback 후
저장된 데이터로 타임라인을 반환해야 한다(첫 조회 500 회귀 방지, issue #132).
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.routers import companies


class _FakeResult:
    def all(self):
        return []


class _FakeScalars:
    def all(self):
        return []


class _FakeDB:
    """sync 이후 쿼리는 빈 결과, rollback 호출을 기록하는 최소 세션."""

    def __init__(self):
        self.rolled_back = False

    def execute(self, stmt):
        return _FakeResult()

    def scalars(self, stmt):
        return _FakeScalars()

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def _dart_key(monkeypatch):
    monkeypatch.setattr(companies, "get_settings", lambda: Settings(dart_api_key="k"))


def test_timeline_rolls_back_and_returns_on_sync_failure(monkeypatch, _dart_key):
    def _boom(db, settings, code, begin, end):
        raise RuntimeError("DART down")

    monkeypatch.setattr(companies.dart_ingest, "sync_disclosures", _boom)

    db = _FakeDB()
    # sync 예외가 전파되지 않고, 빈 타임라인을 정상 반환해야 한다.
    result = companies.company_timeline("093320", db=db)

    assert result == []
    assert db.rolled_back is True  # 세션 정리됨 → 후속 쿼리 오염 없음


def test_timeline_no_rollback_when_sync_ok(monkeypatch, _dart_key):
    monkeypatch.setattr(companies.dart_ingest, "sync_disclosures", lambda *a, **k: 0)
    db = _FakeDB()
    result = companies.company_timeline("093320", db=db)
    assert result == []
    assert db.rolled_back is False
