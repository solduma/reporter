"""US 8-K 공시 수집·조회 + item 라벨 매핑 단위 테스트(SEC 목킹)."""

from __future__ import annotations

from datetime import date

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters import sec
from app.config import Settings
from app.db.models import Base, UsDisclosure
from app.services import us_disclosure_ingest as ing


def test_describe_8k_items():
    assert sec.describe_8k_items("5.02") == "임원 변동"
    assert sec.describe_8k_items("2.02,9.01") == "실적 발표 · 재무제표·첨부"
    assert sec.describe_8k_items("") == "8-K 공시"
    assert sec.describe_8k_items("99.9") == "항목 99.9"  # 미매핑 코드는 그대로


@pytest.fixture
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[UsDisclosure.__table__])
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_sync_8k_upserts_and_dedups(db, monkeypatch):
    filings = [
        sec.Filing(accession="0001045810-26-000060", form="8-K", filing_date="2026-07-02",
                   items="5.02", primary_doc_url="http://sec/nvda1.htm"),
        sec.Filing(accession="0001045810-26-000056", form="8-K", filing_date="2026-06-30",
                   items="2.02,9.01", primary_doc_url="http://sec/nvda2.htm"),
    ]
    monkeypatch.setattr(sec, "resolve_cik", lambda s, t, session=None: 1045810)
    monkeypatch.setattr(sec, "fetch_recent_filings", lambda s, c, forms, limit, session: filings)
    settings = Settings()
    n = ing.sync_8k(db, "NVDA", settings, requests.Session())
    assert n == 2
    rows = ing.recent_disclosures(db, "NVDA")
    assert [r.filing_date for r in rows] == [date(2026, 7, 2), date(2026, 6, 30)]  # 최신순
    assert rows[0].title == "임원 변동"  # item 라벨 저장
    # 재수집은 중복 없음(on_conflict_do_nothing).
    ing.sync_8k(db, "NVDA", settings, requests.Session())
    assert len(ing.recent_disclosures(db, "NVDA")) == 2


def test_sync_8k_unknown_cik_returns_zero(db, monkeypatch):
    monkeypatch.setattr(sec, "resolve_cik", lambda s, t, session=None: None)
    assert ing.sync_8k(db, "ZZZZ", Settings(), requests.Session()) == 0
