"""지분구조(ownership_service) — 주주 명부 + 자회사·출자사 + 최근 변동 캐시 조회.

주주·자회사는 DB 영속분을 읽고 상장 관계사/주주는 이름 역해석. 최근 변동은 12h 캐시(OwnershipChangeCache)
우선 — miss 시에만 elestock live 호출(fetch_ownership_changes) 후 캐싱한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

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
from app.domain.disclosure import OwnershipChange
from app.services import ownership_service as svc


# SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON 으로 렌더해 create_all 통과(test_trend_service 와 동일).
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


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


def _settings(dart_key: str = "key"):
    return type("S", (), {"dart_api_key": dart_key})()


def test_get_ownership_reads_shareholders_and_subsidiaries_with_name_links(db):
    # 상장 주주·자회사는 CorpCodeMap 역해석으로 이름·링크 부여.
    db.add(CorpCodeMap(stock_code="005930", corp_code="00126380", corp_name="삼성전자"))
    db.add(
        Shareholder(
            stock_code="093320",
            holder_name="삼성전자",
            relate="최대주주 본인",
            stake_pct=12.3,
            is_corporate=True,
            related_stock_code="005930",
            bsns_year=2024,
        )
    )
    db.add(
        Shareholder(
            stock_code="093320",
            holder_name="김대표",
            relate="최대주주의 특수관계인",
            stake_pct=1.5,
            is_corporate=False,
            related_stock_code=None,
            bsns_year=2024,
        )
    )
    db.add(
        RelatedCompany(
            stock_code="093320",
            related_name="삼성전자",
            relation="subsidiary",
            stake_pct=80.0,
            related_stock_code="005930",
        )
    )
    db.add(
        RelatedCompany(
            stock_code="093320",
            related_name="㈜비상장",
            relation="investor",
            stake_pct=10.0,
            related_stock_code=None,
        )
    )
    db.commit()

    # 캐시 신선 → elestock 미호출.
    db.add(
        OwnershipChangeCache(
            stock_code="093320",
            payload=[
                {
                    "rcept_no": "20260701000001",
                    "rcept_date": "2026-07-01",
                    "reporter": "김대표",
                    "position": "사장",
                    "shares_delta": 1000,
                    "shares_after": 5000,
                    "reason": "",
                }
            ],
        )
    )
    db.commit()

    out = svc.get_ownership(db, _settings(), "093320")

    # 주주: 지분율 내림차순, 상장 주주만 이름·링크.
    sh = {s.holder_name: s for s in out.shareholders}
    assert next(iter(out.shareholders)).holder_name == "삼성전자"  # 12.3% > 1.5%
    assert sh["삼성전자"].related_stock_name == "삼성전자"
    assert sh["삼성전자"].related_stock_code == "005930"
    assert sh["김대표"].related_stock_name is None
    assert out.as_of_year == 2024

    # 자회사: 상장은 이름·링크, 비상장은 None.
    sub = {s.related_name: s for s in out.subsidiaries}
    assert sub["삼성전자"].related_stock_name == "삼성전자"
    assert sub["㈜비상장"].related_stock_name is None
    assert out.subsidiaries[0].relation == "subsidiary"  # 80% > 10%

    # 캐시 hit → 변동 1건, stale 아님.
    assert out.changes_stale is False
    assert len(out.changes) == 1
    assert out.changes[0].shares_delta == 1000


def test_changes_cache_miss_fetches_elestock_and_caches(db, monkeypatch):
    db.add(CorpCodeMap(stock_code="093320", corp_code="00B", corp_name="케이아이엔엑스"))
    db.commit()

    fetched = {"20260701000001": OwnershipChange("윤원일", "사장", "등기임원", 9214, 3000)}
    calls = {"n": 0}

    def _fake_fetch(api_key, corp_code, session):
        calls["n"] += 1
        return fetched

    monkeypatch.setattr(svc.dart, "fetch_ownership_changes", _fake_fetch)

    out = svc.get_ownership(db, _settings(), "093320")
    assert calls["n"] == 1  # 캐시 miss → 1회 호출
    assert len(out.changes) == 1
    assert out.changes[0].reporter == "윤원일"
    assert out.changes[0].shares_delta == 3000
    assert out.changes[0].rcept_date.isoformat() == "2026-07-01"  # rcept_no 접두 일자

    # 두 번째 호출 — 캐시 hit 로 elestock 미호출.
    out2 = svc.get_ownership(db, _settings(), "093320")
    assert calls["n"] == 1
    assert out2.changes == out.changes

    # 캐시 행이 저장됐는지 확인.
    cached = db.get(OwnershipChangeCache, "093320")
    assert cached is not None and cached.payload[0]["reporter"] == "윤원일"


def test_changes_cache_expired_refetches(db, monkeypatch):
    db.add(CorpCodeMap(stock_code="093320", corp_code="00B", corp_name="케이아이엔엑스"))
    # 13h 전 캐시 → 만료.
    stale = OwnershipChangeCache(
        stock_code="093320",
        payload=[
            {
                "rcept_no": "old",
                "rcept_date": None,
                "reporter": "옛것",
                "position": "",
                "shares_delta": None,
                "shares_after": None,
                "reason": "",
            }
        ],
        updated_at=datetime.now(UTC) - timedelta(hours=13),
    )
    db.add(stale)
    db.commit()

    monkeypatch.setattr(
        svc.dart,
        "fetch_ownership_changes",
        lambda *a, **k: {"20260702000002": OwnershipChange("새것", "부사장", "등기임원", 100, 50)},
    )
    out = svc.get_ownership(db, _settings(), "093320")
    assert len(out.changes) == 1
    assert out.changes[0].reporter == "새것"  # 만료 → 재조회 결과로 교체


def test_changes_quota_exceeded_returns_stale_and_marks_stale(db, monkeypatch):
    # 캐시 만료 상태에서 live 조회가 쿼터초과 → 기존 캐시라도 내보내고 stale 신호.
    db.add(CorpCodeMap(stock_code="093320", corp_code="00B", corp_name="케이아이엔엑스"))
    db.add(
        OwnershipChangeCache(
            stock_code="093320",
            payload=[
                {
                    "rcept_no": "old",
                    "rcept_date": None,
                    "reporter": "옛것",
                    "position": "",
                    "shares_delta": None,
                    "shares_after": None,
                    "reason": "",
                }
            ],
            updated_at=datetime.now(UTC) - timedelta(hours=13),
        )
    )
    db.commit()

    def _raise(*a, **k):
        raise svc.dart.DartQuotaExceeded("020")

    monkeypatch.setattr(svc.dart, "fetch_ownership_changes", _raise)
    out = svc.get_ownership(db, _settings(), "093320")
    assert out.changes_stale is True
    assert out.changes[0].reporter == "옛것"  # 기존 캐시 유지


def test_changes_no_dart_key_marks_stale_without_fetch(db, monkeypatch):
    # dart_api_key 없으면 live 조회 불가 — stale 만 반환(호출 자체 안 함).
    db.add(CorpCodeMap(stock_code="093320", corp_code="00B", corp_name="케이아이엔엑스"))
    db.commit()
    calls = {"n": 0}
    monkeypatch.setattr(
        svc.dart,
        "fetch_ownership_changes",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {},
    )
    out = svc.get_ownership(db, _settings(dart_key=""), "093320")
    assert out.changes == [] and out.changes_stale is True
    assert calls["n"] == 0
