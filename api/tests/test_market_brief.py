"""build_market_brief 단위 테스트 — 국내 마감시황 제외 + market_date=실행일 (issue #43).

크롤·PDF·GLM·DB 를 모두 스텁으로 대체해 필터·날짜 로직만 검증(실 자원 미사용).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.services import ingest
from reporter.models import Report as CrawledReport


def _cr(title: str) -> CrawledReport:
    return CrawledReport(
        category="market_info", title=title, broker="b", date="26.07.09", views=1,
        pdf_url="http://x/p.pdf",
    )


class _FakeSession:
    def __init__(self):
        self.added = []

    def scalar(self, stmt):
        return None  # 기존 행 없음 → insert 경로

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


@pytest.fixture
def _stub(monkeypatch):
    """크롤 결과를 주입하고 PDF/GLM 단계를 통과시키는 스텁."""

    def _apply(crawled):
        monkeypatch.setattr(ingest, "crawl_categories", lambda cats, target_date=None: crawled)
        monkeypatch.setattr(ingest, "_download_pdf", lambda url, s: b"pdfbytes")
        monkeypatch.setattr(ingest, "extract_text_from_bytes", lambda b, n: "본문")
        monkeypatch.setattr(ingest, "OllamaClient", lambda host, key: object())
        # summarize_reports 는 받은 리포트를 그대로(요약 채워) 반환하도록
        monkeypatch.setattr(ingest.analyzer, "summarize_reports", lambda c, m, reps: reps)

        class _Briefing:
            text = "종합"

        monkeypatch.setattr(ingest.analyzer, "synthesize_insight", lambda c, m, reps: _Briefing())

    return _apply


def _settings() -> Settings:
    return Settings(ollama_api_key="k")


def test_excludes_domestic_closing_from_morning_brief(_stub, monkeypatch):
    captured = {}
    _stub([
        _cr("Daily Morning Brief(2026.07.09)"),
        _cr("국내주식 마감 시황 (26.07.08)"),
        _cr("유안타 AI 미국 주식시장 마감 시황"),
    ])
    # summarize 가 받는(=필터 후) 리포트 포착
    monkeypatch.setattr(ingest.analyzer, "summarize_reports", lambda c, m, reps: captured.setdefault("reps", reps) or reps)

    db = _FakeSession()
    ingest.build_market_brief(db, _settings())

    titles = [r.title for r in captured["reps"]]
    assert "국내주식 마감 시황 (26.07.08)" not in titles  # 전일 국내 마감 제외
    assert "Daily Morning Brief(2026.07.09)" in titles
    assert "유안타 AI 미국 주식시장 마감 시황" in titles  # 미국 마감은 유지


def test_market_date_is_run_date_not_list_top(_stub, monkeypatch):
    # 리스트 최상단 발행일이 무엇이든 market_date 는 실행일로 고정
    _stub([_cr("Daily Morning Brief(2026.07.09)")])
    monkeypatch.setattr(ingest, "datetime", _FixedDatetime)

    db = _FakeSession()
    ingest.build_market_brief(db, _settings())

    assert len(db.added) == 1
    assert db.added[0].market_date == date(2026, 7, 9)


def test_returns_none_when_only_domestic_closing(_stub):
    _stub([_cr("국내주식 마감 시황 (26.07.08)")])
    db = _FakeSession()
    assert ingest.build_market_brief(db, _settings()) is None  # 오전 대상 0건
    assert db.added == []


class _FixedDatetime:
    @staticmethod
    def now():
        import datetime as _dt

        return _dt.datetime(2026, 7, 9, 9, 30)
