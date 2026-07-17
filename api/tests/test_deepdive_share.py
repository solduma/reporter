"""딥다이브 무인증 공유 스냅샷 — 생성·조회·만료·스냅샷 고정 검증."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DeepDiveReport, DeepDiveShare
from app.services.deepdive import share


# SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON(TEXT) 로 렌더해 create_all 을 통과시킨다.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db(monkeypatch):
    # 격리 in-memory SQLite. JSONB 는 위 @compiles 훅으로 JSON 컬럼으로 생성된다.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DeepDiveReport.__table__, DeepDiveShare.__table__])
    session = sessionmaker(bind=engine)()
    # 종목명 해석은 외부 조회이므로 고정.
    monkeypatch.setattr(share.company_service, "resolve_stock_name", lambda _db, _c: "테스트종목")
    yield session
    session.close()


def _report(**kw):
    base = {
        "stock_code": "000000",
        "model": "test",
        "narrative_md": "# 결론\n좋음",
        "verdict": "성장주 · 업사이드 42%",
        "upside_pct": 42.0,
        "overview_json": {"a": 1},
        "valuation_json": {"final_target_price": 1000},
    }
    base.update(kw)
    return DeepDiveReport(**base)


def test_create_and_fetch_roundtrip(db):
    db.add(_report())
    db.commit()

    created = share.create_share(db, "000000")
    assert created is not None
    assert len(created.token) >= 40  # token_urlsafe(32)
    assert created.stock_name == "테스트종목"

    found = share.get_valid_share(db, created.token)
    assert found is not None
    # payload 스냅샷이 보고서 내용을 그대로 담는다(narrative·verdict·단계 JSON).
    assert found.payload_json["verdict"] == "성장주 · 업사이드 42%"
    assert found.payload_json["narrative_md"] == "# 결론\n좋음"
    assert found.payload_json["valuation"]["final_target_price"] == 1000


def test_no_report_returns_none(db):
    assert share.create_share(db, "999999") is None


def test_expired_share_not_returned(db):
    db.add(_report())
    db.commit()
    created = share.create_share(db, "000000")
    # 만료 시각을 과거로 강제.
    created.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert share.get_valid_share(db, created.token) is None


def test_unknown_token_returns_none(db):
    assert share.get_valid_share(db, "nonexistent-token") is None


def test_snapshot_frozen_after_reanalysis(db):
    """공유 후 보고서가 갱신돼도 스냅샷은 생성 시점 내용을 유지한다."""
    rep = _report(verdict="원본 결론")
    db.add(rep)
    db.commit()
    created = share.create_share(db, "000000")

    # 재분석으로 보고서 verdict 변경.
    rep.verdict = "변경된 결론"
    db.commit()

    found = share.get_valid_share(db, created.token)
    assert found.payload_json["verdict"] == "원본 결론"
