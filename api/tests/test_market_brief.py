"""build_market_brief 단위 테스트 — 3-상태(개장전/장중/마감) 종합 + market_date=실행일.

크롤·PDF·GLM·DB·뉴스·지수를 모두 스텁으로 대체해 국면 분기·근거·날짜 로직만 검증(실 자원 미사용).
"""

from __future__ import annotations

from datetime import date, datetime

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


class _Briefing:
    text = "종합"


@pytest.fixture
def _stub(monkeypatch):
    """크롤 결과를 주입하고 PDF/GLM/뉴스 단계를 통과시키는 스텁."""

    def _apply(crawled):
        monkeypatch.setattr(ingest, "crawl_categories", lambda cats, target_date=None: crawled)
        monkeypatch.setattr(ingest, "_download_pdf", lambda url, s: b"pdfbytes")
        monkeypatch.setattr(ingest, "extract_text_from_bytes", lambda b, n: "본문")
        monkeypatch.setattr(ingest, "get_llm", lambda settings: object())  # LLMPort 스텁(non-None)
        monkeypatch.setattr(ingest.analyzer, "summarize_reports", lambda c, m, reps: reps)
        # 뉴스는 기본적으로 없음(각 테스트가 필요 시 오버라이드).
        monkeypatch.setattr(
            ingest.news, "collect", lambda kw, limit, session=None, max_age_hours=None: []
        )
        monkeypatch.setattr(ingest.analyzer, "synthesize_forecast", lambda c, m, reps: _Briefing())
        monkeypatch.setattr(
            ingest.analyzer, "synthesize_closing_review", lambda c, m, reps: _Briefing()
        )

    return _apply


def _settings() -> Settings:
    return Settings(ollama_api_key="k")


def test_market_phase_boundaries():
    assert ingest._market_phase(datetime(2026, 7, 10, 8, 50)) == "forecast"
    assert ingest._market_phase(datetime(2026, 7, 10, 9, 0)) == "forecast"  # 09:00 은 아직 개장 전
    assert ingest._market_phase(datetime(2026, 7, 10, 9, 30)) == "intraday"
    assert ingest._market_phase(datetime(2026, 7, 10, 13, 0)) == "intraday"
    assert ingest._market_phase(datetime(2026, 7, 10, 16, 0)) == "closing"
    assert ingest._market_phase(datetime(2026, 7, 10, 18, 30)) == "closing"


def test_forecast_uses_all_reports_before_close(_stub, monkeypatch):
    captured = {}
    _stub([
        _cr("Daily Morning Brief(2026.07.09)"),
        _cr("국내주식 마감 시황 (26.07.08)"),
        _cr("유안타 AI 미국 주식시장 마감 시황"),
    ])
    monkeypatch.setattr(
        ingest.analyzer, "summarize_reports",
        lambda c, m, reps: captured.setdefault("reps", reps) or reps,
    )

    db = _FakeSession()
    ingest.build_market_brief(db, _settings(), phase="forecast")

    titles = [r.title for r in captured["reps"]]
    assert "국내주식 마감 시황 (26.07.08)" in titles
    assert "유안타 AI 미국 주식시장 마감 시황" in titles
    assert "Daily Morning Brief(2026.07.09)" in titles
    assert db.added[0].phase == "forecast"


def test_after_close_uses_only_domestic_closing_and_review(_stub, monkeypatch):
    captured = {}
    _stub([
        _cr("Daily Morning Brief(2026.07.09)"),
        _cr("국내주식 마감 시황 (26.07.09)"),
        _cr("유안타 AI 미국 주식시장 마감 시황"),
    ])
    monkeypatch.setattr(
        ingest.analyzer, "summarize_reports",
        lambda c, m, reps: captured.setdefault("reps", reps) or reps,
    )

    def _mark_review(c, m, reps):
        captured["used"] = "review"
        return _Stub()

    monkeypatch.setattr(ingest.analyzer, "synthesize_closing_review", _mark_review)

    db = _FakeSession()
    ingest.build_market_brief(db, _settings(), phase="closing")

    titles = [r.title for r in captured["reps"]]
    # 마감 후: 오늘 국내 마감시황만 근거(미장·오전은 제외). 뉴스는 stub 이 빈 리스트.
    assert titles == ["국내주식 마감 시황 (26.07.09)"]
    assert captured["used"] == "review"


class _Stub:
    text = "리뷰"


def test_after_close_falls_back_when_no_domestic_closing(_stub):
    # 마감 후인데 국내 마감시황이 아직 없으면 전체로 폴백(빈 화면 방지).
    _stub([_cr("유안타 AI 미국 주식시장 마감 시황")])
    db = _FakeSession()
    assert ingest.build_market_brief(db, _settings(), phase="closing") == "종합"
    assert len(db.added) == 1


def test_intraday_uses_live_quotes_and_news_not_research(_stub, monkeypatch):
    # 장중: 리서치 크롤을 아예 안 쓰고 실시간 지수·뉴스만 근거로 종합한다.
    captured = {}

    def _fail_crawl(cats, target_date=None):
        captured["crawled"] = True
        return []

    monkeypatch.setattr(ingest, "crawl_categories", _fail_crawl)
    monkeypatch.setattr(ingest.us_market, "fetch_kr_indices", lambda s: [_Quote("코스피", "2,650", "0.45")])
    monkeypatch.setattr(ingest.us_market, "fetch_exchange_rates", lambda s: [_Quote("원/달러", "1,380", "-0.2")])
    monkeypatch.setattr(ingest.us_market, "fetch_us_indices", lambda s: [])
    monkeypatch.setattr(
        ingest.news, "collect",
        lambda kw, limit, session=None, max_age_hours=None: [_NewsItem("삼성전자 신고가")],
    )
    monkeypatch.setattr(ingest, "article", _FakeArticle)

    def _capture_intraday(client, model, quote_lines, news_blocks, prev_summary=None):
        captured["quotes"] = quote_lines
        captured["news"] = news_blocks
        captured["prev"] = prev_summary
        return _Briefing()

    monkeypatch.setattr(ingest.analyzer, "synthesize_intraday", _capture_intraday)

    db = _FakeSession()
    result = ingest.build_market_brief(db, _settings(), phase="intraday")

    assert result == "종합"
    assert "crawled" not in captured  # 리서치 크롤 미호출
    assert any("코스피" in q for q in captured["quotes"])
    assert any("원/달러" in q for q in captured["quotes"])
    assert captured["news"]  # 뉴스 블록 존재
    assert db.added[0].phase == "intraday"


def test_intraday_returns_none_when_no_live_data(_stub, monkeypatch):
    monkeypatch.setattr(ingest.us_market, "fetch_kr_indices", lambda s: [])
    monkeypatch.setattr(ingest.us_market, "fetch_exchange_rates", lambda s: [])
    monkeypatch.setattr(ingest.us_market, "fetch_us_indices", lambda s: [])
    monkeypatch.setattr(
        ingest.news, "collect", lambda kw, limit, session=None, max_age_hours=None: []
    )
    db = _FakeSession()
    assert ingest.build_market_brief(db, _settings(), phase="intraday") is None
    assert db.added == []


def test_intraday_passes_prev_summary_for_contrast(_stub, monkeypatch):
    # 장중 갱신 시 당일 직전 시황을 synthesize_intraday 에 prev_summary 로 넘겨 '장초→현재' 대조 유도.
    captured = {}
    monkeypatch.setattr(ingest, "crawl_categories", lambda cats, target_date=None: [])
    monkeypatch.setattr(ingest.us_market, "fetch_kr_indices", lambda s: [_Quote("코스피", "2,650", "0.45")])
    monkeypatch.setattr(ingest.us_market, "fetch_exchange_rates", lambda s: [])
    monkeypatch.setattr(ingest.us_market, "fetch_us_indices", lambda s: [])
    monkeypatch.setattr(
        ingest.news, "collect",
        lambda kw, limit, session=None, max_age_hours=None: [_NewsItem("장중 뉴스")],
    )
    monkeypatch.setattr(ingest, "article", _FakeArticle)

    def _capture(client, model, quote_lines, news_blocks, prev_summary=None):
        captured["prev"] = prev_summary
        return _Briefing()

    monkeypatch.setattr(ingest.analyzer, "synthesize_intraday", _capture)

    # 당일 기존 시황이 있는 세션(update 경로) — 그 summary 가 prev 로 전달돼야 한다.
    class _Existing:
        summary = "장 초엔 강세로 출발"
        phase = "intraday"

    db = _FakeSession()
    db.scalar = lambda stmt: _Existing()  # 기존 행 존재
    ingest.build_market_brief(db, _settings(), phase="intraday")
    assert captured["prev"] == "장 초엔 강세로 출발"
    # 과거 백필(target_date 지정)은 장중 실시간이 불가하므로 마감 리뷰 경로.
    _stub([_cr("국내주식 마감 시황 (26.07.08)")])
    db = _FakeSession()
    ingest.build_market_brief(db, _settings(), target_date="26.07.08")
    assert db.added[0].phase == "closing"
    assert db.added[0].market_date == date(2026, 7, 8)


def test_market_date_is_run_date_not_list_top(_stub, monkeypatch):
    _stub([_cr("Daily Morning Brief(2026.07.09)")])
    monkeypatch.setattr(ingest, "datetime", _FixedDatetime)

    db = _FakeSession()
    ingest.build_market_brief(db, _settings(), phase="forecast")

    assert len(db.added) == 1
    assert db.added[0].market_date == date(2026, 7, 8)


def test_returns_none_when_no_reports(_stub):
    _stub([])
    db = _FakeSession()
    assert ingest.build_market_brief(db, _settings(), phase="forecast") is None
    assert db.added == []


class _Quote:
    def __init__(self, name, close, ratio):
        self.name, self.close, self.change_ratio = name, close, ratio


class _NewsItem:
    def __init__(self, title):
        self.title, self.source, self.link = title, "src", "http://x"


class _FakeArticle:
    @staticmethod
    def fetch_article_text(url):
        return "기사 본문"


class _FixedDatetime:
    @staticmethod
    def now():
        import datetime as _dt

        return _dt.datetime(2026, 7, 8, 9, 30)
