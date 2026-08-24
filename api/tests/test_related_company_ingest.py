"""관계사 수집 배치 — 상장 관계사 역매핑·related_names 조회·정규화."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.adapters import dart
from app.db.models import (
    Base,
    CorpCodeMap,
    DilutionCache,
    Financial,
    MajorHolderCache,
    OwnershipChangeCache,
    OwnershipSummary,
    RelatedCompany,
    Shareholder,
)
from app.services import related_company_ingest as rci


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            CorpCodeMap.__table__,
            DilutionCache.__table__,
            Financial.__table__,
            MajorHolderCache.__table__,
            OwnershipChangeCache.__table__,
            OwnershipSummary.__table__,
            RelatedCompany.__table__,
            Shareholder.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_norm_strips_corp_suffixes():
    assert rci._norm("㈜가비아") == "가비아"
    assert rci._norm("주식회사 에스피소프트") == "에스피소프트"
    assert rci._norm("(주)엑스게이트") == "엑스게이트"


def test_backfill_maps_listed_related_and_stores(db, monkeypatch):
    # 관계사명이 CorpCodeMap 에 있으면 related_stock_code 링크, 없으면 None.
    db.add(CorpCodeMap(stock_code="079940", corp_code="00A", corp_name="가비아"))
    db.add(CorpCodeMap(stock_code="093320", corp_code="00B", corp_name="케이아이엔엑스"))
    db.commit()

    # hyslr_rows(주주 명부)와 related(모/자회사·출자사)를 같이 모킹 — ingest 가 한 번의 hyslr
    # 호출로 두 테이블을 채우는 구조를 반영.
    monkeypatch.setattr(
        rci.dart,
        "fetch_hyslr_rows",
        lambda key, cc, y, q, s: [
            dart.HyslrRow("(주)가비아", "최대주주 본인", 36.3, True),
            dart.HyslrRow("김대표", "최대주주의 특수관계인", 1.5, False),
        ],
    )
    monkeypatch.setattr(
        rci.dart,
        "fetch_related_companies",
        lambda key, cc, y, q, s, *, hyslr_rows=None: [
            dart.RelatedParty("(주)가비아", "parent", 36.3),
            dart.RelatedParty("㈜비상장자회사", "subsidiary", 80.0),
        ],
    )
    # corp_code 조회는 CorpCodeMap 에서(093320 → 00B).
    settings = type("S", (), {"dart_api_key": "key"})()
    ok = rci.backfill_stock(db, settings, "093320", rci._corp_name_to_stock(db))
    assert ok is True

    rows = db.scalars(select(RelatedCompany).where(RelatedCompany.stock_code == "093320")).all()
    by_name = {r.related_name: r for r in rows}
    assert by_name["(주)가비아"].relation == "parent"
    assert by_name["(주)가비아"].related_stock_code == "079940"  # 상장 → 역매핑
    assert by_name["㈜비상장자회사"].related_stock_code is None  # 비상장 → None

    # 주주 명부(Shareholder)도 같은 hyslr 호출로 upsert — 법인 주주만 역매핑.
    sh = db.scalars(select(Shareholder).where(Shareholder.stock_code == "093320")).all()
    sh_by_name = {s.holder_name: s for s in sh}
    assert sh_by_name["(주)가비아"].related_stock_code == "079940"
    assert sh_by_name["(주)가비아"].is_corporate is True
    assert sh_by_name["김대표"].is_corporate is False
    assert sh_by_name["김대표"].related_stock_code is None  # 개인 → 링크 없음


def test_backfill_no_corp_code_marks_done(db, monkeypatch):
    # corp_code 매핑 없으면 True(완료 처리, 재시도 불필요).
    called = {"n": 0}
    monkeypatch.setattr(
        rci.dart,
        "fetch_related_companies",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [],
    )
    settings = type("S", (), {"dart_api_key": "key"})()
    assert rci.backfill_stock(db, settings, "999999", {}) is True
    assert called["n"] == 0  # DART 조회 안 함


def test_related_names_returns_all(db):
    db.add_all(
        [
            RelatedCompany(stock_code="093320", related_name="(주)가비아", relation="parent"),
            RelatedCompany(stock_code="093320", related_name="㈜에스피소프트", relation="investor"),
        ]
    )
    db.commit()
    names = rci.related_names(db, "093320")
    assert set(names) == {"(주)가비아", "㈜에스피소프트"}
