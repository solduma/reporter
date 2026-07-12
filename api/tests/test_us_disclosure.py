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


class _FakeUsDisclosures:
    """UsDisclosurePort 를 만족하는 fake — 네트워크 없이 정해둔 CIK/filings 반환."""

    def __init__(self, cik, filings):
        self._cik = cik
        self._filings = filings

    def resolve_cik(self, ticker, session=None):
        return self._cik

    def fetch_recent_filings(self, cik, forms=("8-K",), limit=20, session=None):
        return self._filings

    def describe_8k_items(self, items):
        return sec.describe_8k_items(items)


def test_sync_8k_upserts_and_dedups(db, monkeypatch):
    filings = [
        sec.Filing(accession="0001045810-26-000060", form="8-K", filing_date="2026-07-02",
                   items="5.02", primary_doc_url="http://sec/nvda1.htm"),
        sec.Filing(accession="0001045810-26-000056", form="8-K", filing_date="2026-06-30",
                   items="2.02,9.01", primary_doc_url="http://sec/nvda2.htm"),
    ]
    # 포트 seam 에 fake 주입(치환성) — 네트워크·실 SEC 미접속.
    monkeypatch.setattr(ing, "_disclosures", lambda settings: _FakeUsDisclosures(1045810, filings))
    settings = Settings()
    n = ing.sync_8k(db, "NVDA", settings, requests.Session())
    assert n == 2
    rows = ing.recent_disclosures(db, "NVDA")
    assert [r.filing_date for r in rows] == [date(2026, 7, 2), date(2026, 6, 30)]  # 최신순
    assert rows[0].title == "임원 변동"  # item 라벨 저장
    # 재수집은 중복 없음(on_conflict_do_nothing) + saved 카운트도 0(실제 삽입분만).
    again = ing.sync_8k(db, "NVDA", settings, requests.Session())
    assert again == 0
    assert len(ing.recent_disclosures(db, "NVDA")) == 2


def test_sync_8k_unknown_cik_returns_zero(db, monkeypatch):
    monkeypatch.setattr(ing, "_disclosures", lambda settings: _FakeUsDisclosures(None, []))
    assert ing.sync_8k(db, "ZZZZ", Settings(), requests.Session()) == 0
