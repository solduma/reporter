"""재무 10년 백필 마커 복원(reconcile) — psr 데이터가 있으면 DART 재조회 없이 완료 마커 복원."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.adapters.dart import throttle as dart_throttle
from app.db.models import Base, Financial, SyncState
from app.services import financials_backfill


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Financial.__table__, SyncState.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_reconcile_restores_marker_for_psr_without_marker(db):
    # psr 이 있는데 마커가 없는 종목 → 마커 복원(백필이 과거에 완료했으나 마커만 삭제된 경우).
    db.add(Financial(stock_code="000001", period="2025.12", is_estimate=False, psr=1.5))
    db.commit()

    restored = financials_backfill._reconcile_markers(db, ["000001"], set())
    assert restored == 1
    marked = db.scalars(
        select(SyncState.stock_code).where(SyncState.domain == "financials_10y")
    ).all()
    assert marked == ["000001"]


def test_reconcile_skips_when_no_psr(db):
    # psr 없는 종목(백필 미완 or 밸류 산출 불가)은 복원하지 않는다 → 정상 백필 대상 유지.
    db.add(Financial(stock_code="000002", period="2025.12", is_estimate=False, psr=None))
    db.commit()

    restored = financials_backfill._reconcile_markers(db, ["000002"], set())
    assert restored == 0


def test_reconcile_ignores_already_marked(db):
    # 이미 마커 있는 종목은 재복원 대상이 아니다.
    db.add(Financial(stock_code="000003", period="2025.12", is_estimate=False, psr=2.0))
    db.commit()

    restored = financials_backfill._reconcile_markers(db, ["000003"], {"000003"})
    assert restored == 0


def test_backfill_budget_exhausted_threshold(monkeypatch):
    # 예산 미만이면 계속, 도달하면 조기 중단 신호.
    monkeypatch.setattr(dart_throttle, "calls_today", lambda: dart_throttle.BACKFILL_DAILY_BUDGET - 1)
    assert dart_throttle.backfill_budget_exhausted() is False
    monkeypatch.setattr(dart_throttle, "calls_today", lambda: dart_throttle.BACKFILL_DAILY_BUDGET)
    assert dart_throttle.backfill_budget_exhausted() is True
