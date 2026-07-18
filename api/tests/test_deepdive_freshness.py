"""딥다이브 재무 신선화 — 재무 지문·EBITDA 성장축 재계산·inputs_hash 반영 검증."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Financial, GrowthMetric
from app.services import growth_ingest
from app.services.deepdive import freshness, orchestrator


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Financial.__table__, GrowthMetric.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _fin(period, ebitda=None, revenue=None, updated=None):
    return Financial(
        stock_code="000000", period=period, is_estimate=False,
        ebitda=ebitda, revenue=revenue, updated_at=updated,
    )


def test_fingerprint_changes_when_financials_updated(db):
    t1 = datetime(2026, 7, 17, 3, 0, tzinfo=UTC)
    db.add(_fin("2025.12", updated=t1))
    db.commit()
    fp1 = freshness.financials_fingerprint(db, "000000")

    # updated_at 이 바뀌면(재수집) 지문도 바뀐다.
    row = db.query(Financial).first()
    row.updated_at = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)
    db.commit()
    fp2 = freshness.financials_fingerprint(db, "000000")
    assert fp1 != fp2


def test_fingerprint_stable_when_unchanged(db):
    t1 = datetime(2026, 7, 17, 3, 0, tzinfo=UTC)
    db.add(_fin("2025.12", updated=t1))
    db.commit()
    assert freshness.financials_fingerprint(db, "000000") == freshness.financials_fingerprint(db, "000000")


def test_inputs_hash_reflects_fingerprint():
    # 같은 코드·모델·날짜라도 재무 지문이 다르면 inputs_hash 가 달라진다(재생성 판정 반영).
    h1 = orchestrator._inputs_hash("000000", "m", "fpAAAA")
    h2 = orchestrator._inputs_hash("000000", "m", "fpBBBB")
    assert h1 != h2
    # 지문 없으면(기본) 종전과 호환.
    assert orchestrator._inputs_hash("000000", "m") == orchestrator._inputs_hash("000000", "m", "")


def test_refresh_skips_report_backfill_when_unchanged(db, monkeypatch):
    # 재무 stale 이지만 sync 후에도 지문이 안 바뀌고 이미 백필된 종목이면 무거운 report
    # backfill 을 재실행하지 않는다(DART 원문 재다운로드 낭비 방지).
    monkeypatch.setattr(freshness.company_service, "financials_fresh", lambda *a, **k: False)
    monkeypatch.setattr(freshness.company_service, "sync_financials", lambda *a, **k: None)
    monkeypatch.setattr(freshness.company_service, "report_10y_done", lambda *a, **k: True)
    monkeypatch.setattr(freshness.growth_ingest, "refresh_ebitda_axis", lambda *a, **k: False)
    called = {"backfill": 0}

    def _backfill(*a, **k):
        called["backfill"] += 1
        return True

    monkeypatch.setattr(freshness.report_ingest, "backfill_stock", _backfill)
    freshness.refresh(db, object(), "000000")
    assert called["backfill"] == 0  # 미변경 + 이미 완료 → 스킵


def test_refresh_runs_report_backfill_when_not_done(db, monkeypatch):
    # 아직 한 번도 백필 안 된 종목은 재무 미변경이라도 최초 1회 백필한다.
    monkeypatch.setattr(freshness.company_service, "financials_fresh", lambda *a, **k: False)
    monkeypatch.setattr(freshness.company_service, "sync_financials", lambda *a, **k: None)
    monkeypatch.setattr(freshness.company_service, "report_10y_done", lambda *a, **k: False)
    monkeypatch.setattr(freshness.growth_ingest, "refresh_ebitda_axis", lambda *a, **k: False)
    monkeypatch.setattr(freshness.sync_state, "mark", lambda *a, **k: None)
    called = {"backfill": 0}

    def _backfill(*a, **k):
        called["backfill"] += 1
        return True

    monkeypatch.setattr(freshness.report_ingest, "backfill_stock", _backfill)
    freshness.refresh(db, object(), "000000")
    assert called["backfill"] == 1


def test_refresh_ebitda_axis_fills_from_db(db):
    # 연간 EBITDA 2개 → EBITDA 성장축 산출. growth_metric 행이 미리 있어야 update 가 걸린다.
    db.add_all([
        _fin("2024.12", ebitda=100.0, revenue=1000.0),
        _fin("2025.12", ebitda=160.0, revenue=1100.0),
    ])
    db.add(GrowthMetric(stock_code="000000", period="2026.03"))
    db.commit()

    assert growth_ingest.refresh_ebitda_axis(db, "000000") is True
    db.commit()
    row = db.query(GrowthMetric).first()
    assert row.ebitda_status == "흑자지속"  # 100 → 160 둘 다 흑자
    assert row.ebitda_margin_delta == round(160 / 1100 - 100 / 1000, 4)


def test_refresh_ebitda_axis_false_when_single_annual(db):
    db.add(_fin("2025.12", ebitda=160.0, revenue=1100.0))
    db.add(GrowthMetric(stock_code="000000", period="2026.03"))
    db.commit()
    assert growth_ingest.refresh_ebitda_axis(db, "000000") is False
