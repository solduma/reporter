import json
from pathlib import Path

import pytest

import reporter.pipeline as pipeline
from reporter.config import Config
from reporter.models import Briefing, DigestResult, Report


def _spool_entries(config: Config) -> list[dict]:
    path = config.logs_dir / "broadcasts.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _config(tmp_path: Path) -> Config:
    return Config(
        ollama_host="https://ollama.com",
        ollama_api_key="key",
        summary_model="s",
        insight_model="i",
        telegram_bot_token="token",
        telegram_chat_id="123",
        root=tmp_path,
    )


def _report(text: str = "본문", summary: str = "") -> Report:
    r = Report(category="company", title="t", broker="삼성증권", date="26.07.07", views=1)
    r.text = text
    r.summary = summary
    return r


@pytest.fixture
def stub_pipeline(monkeypatch):
    """네트워크·GLM 단계를 전부 스텁으로 대체하고, 각 단계 반환값을 주입한다."""

    def _apply(*, crawled, enriched, summarized, briefing=None):
        monkeypatch.setattr(pipeline, "crawl_categories", lambda cats: crawled)
        monkeypatch.setattr(pipeline, "select_top", lambda reports, top_n: reports)
        monkeypatch.setattr(pipeline, "enrich_with_text", lambda selected: enriched)
        monkeypatch.setattr(pipeline, "OllamaClient", lambda host, key: object())
        monkeypatch.setattr(
            pipeline.analyzer, "summarize_reports", lambda c, m, reports: summarized
        )
        if briefing is not None:
            monkeypatch.setattr(
                pipeline.analyzer, "synthesize_insight", lambda c, m, reports: briefing
            )
        sent = []
        monkeypatch.setattr(
            pipeline, "TelegramSender", lambda token, chat_id: _RecordingSender(sent)
        )
        return sent

    return _apply


class _RecordingSender:
    def __init__(self, sink: list):
        self._sink = sink

    def send(self, message: str) -> int:
        self._sink.append(message)
        return 1


def test_returns_none_when_no_reports_crawled(stub_pipeline, tmp_path):
    # 이후 단계에 값을 채워도 크롤 0건이면 즉시 None → 첫 가드를 단독으로 검증
    briefing = Briefing(text="x", report_count=1, categories=["company"])
    sent = stub_pipeline(
        crawled=[], enriched=[_report()], summarized=[_report(summary="s")], briefing=briefing
    )
    result = pipeline.run_morning_briefing(_config(tmp_path), ["company"])
    assert result is None
    assert sent == []


def test_returns_none_when_no_pdf_text(stub_pipeline, tmp_path):
    sent = stub_pipeline(crawled=[_report()], enriched=[], summarized=[])
    result = pipeline.run_morning_briefing(_config(tmp_path), ["company"])
    assert result is None
    assert sent == []


def test_returns_none_when_no_summaries(stub_pipeline, tmp_path):
    sent = stub_pipeline(crawled=[_report()], enriched=[_report()], summarized=[])
    result = pipeline.run_morning_briefing(_config(tmp_path), ["company"])
    assert result is None
    assert sent == []


def test_happy_path_sends_and_logs(stub_pipeline, tmp_path):
    briefing = Briefing(text="핵심 인사이트", report_count=1, categories=["company"])
    sent = stub_pipeline(
        crawled=[_report()],
        enriched=[_report()],
        summarized=[_report(summary="요약")],
        briefing=briefing,
    )
    config = _config(tmp_path)

    result = pipeline.run_morning_briefing(config, ["company"])

    # 발송된 메시지와 반환값이 같고, 브리핑 본문을 포함하며, 로그 파일에 기록된다
    assert result is not None
    assert "핵심 인사이트" in result
    assert sent == [result]
    logged = (config.logs_dir / "today_briefing.txt").read_text(encoding="utf-8")
    assert logged == result

    # 발송과 동시에 브로드캐스트 스풀에도 아카이브된다
    entries = _spool_entries(config)
    assert len(entries) == 1
    assert entries[0]["kind"] == "morning"
    assert "핵심 인사이트" in entries[0]["body"]


def test_message_contains_header_metadata(stub_pipeline, tmp_path):
    briefing = Briefing(text="본문", report_count=3, categories=["company", "industry"])
    stub_pipeline(
        crawled=[_report()],
        enriched=[_report()],
        summarized=[_report(summary="요약")],
        briefing=briefing,
    )
    result = pipeline.run_morning_briefing(_config(tmp_path), ["company"])
    assert "리포트 3건" in result
    assert "종목분석" in result and "산업분석" in result


def _linked_report(title: str, views: int, summary: str, url: str, stock: str | None = None):
    r = Report(
        category="company",
        title=title,
        broker="삼성증권",
        date="26.07.07",
        views=views,
        read_url=url,
        stock_name=stock,
    )
    r.text = "본문"
    r.summary = summary
    return r


@pytest.fixture
def stub_per_report(monkeypatch):
    """per-report 경로의 크롤·PDF·요약·발송을 스텁으로 대체한다."""

    def _apply(*, crawled, enriched=None, summarized=None):
        captured = {}

        def _crawl(cats, target_date=None):
            captured["target_date"] = target_date
            captured["categories"] = cats
            return crawled

        monkeypatch.setattr(pipeline, "crawl_categories", _crawl)
        monkeypatch.setattr(
            pipeline, "enrich_with_text", lambda reports: crawled if enriched is None else enriched
        )
        monkeypatch.setattr(pipeline, "OllamaClient", lambda host, key: object())
        monkeypatch.setattr(
            pipeline.analyzer,
            "summarize_reports",
            lambda c, m, reports: reports if summarized is None else summarized,
        )
        sent = []
        monkeypatch.setattr(
            pipeline, "TelegramSender", lambda token, chat_id: _RecordingSender(sent)
        )
        captured["sent"] = sent
        return captured

    return _apply


def test_per_report_sends_one_message_per_report(stub_per_report, tmp_path):
    reports = [
        _linked_report("리포트A", 100, "요약A", "http://naver/a"),
        _linked_report("리포트B", 200, "요약B", "http://naver/b"),
    ]
    cap = stub_per_report(crawled=reports)

    count = pipeline.run_per_report_briefing(_config(tmp_path), ["company"], target_date="26.07.07")

    assert count == 2
    assert len(cap["sent"]) == 2
    # 각 메시지에 요약과 링크가 포함된다
    joined = "\n".join(cap["sent"])
    assert "요약A" in joined and "요약B" in joined
    assert "http://naver/a" in joined and "http://naver/b" in joined
    assert cap["target_date"] == "26.07.07"


def test_per_report_sorts_by_views_desc(stub_per_report, tmp_path):
    reports = [
        _linked_report("낮음", 50, "s1", "http://a"),
        _linked_report("높음", 900, "s2", "http://b"),
        _linked_report("중간", 300, "s3", "http://c"),
    ]
    cap = stub_per_report(crawled=reports)

    pipeline.run_per_report_briefing(_config(tmp_path), ["company"])

    # 조회수 높은 순으로 발송되어야 한다
    order = [msg.split("\n")[0] for msg in cap["sent"]]
    assert "높음" in order[0]
    assert "중간" in order[1]
    assert "낮음" in order[2]


def test_per_report_returns_zero_without_reports(stub_per_report, tmp_path):
    cap = stub_per_report(crawled=[])
    assert pipeline.run_per_report_briefing(_config(tmp_path), ["company"]) == 0
    assert cap["sent"] == []


def test_per_report_message_omits_link_when_absent():
    r = Report(category="industry", title="반도체 전망", broker="KB증권", date="26.07.07", views=1)
    r.summary = "핵심 요약"
    msg = pipeline._format_report_message(r)
    assert "산업분석" in msg
    assert "반도체 전망" in msg
    assert "핵심 요약" in msg
    assert "🔗" not in msg  # read_url 이 없으면 링크 줄을 넣지 않는다


def test_per_report_message_includes_stock_name():
    r = Report(
        category="company",
        title="목표가 상향",
        broker="삼성증권",
        date="26.07.07",
        views=1,
        read_url="http://x",
        stock_name="삼성전자",
    )
    r.summary = "요약"
    msg = pipeline._format_report_message(r)
    assert "삼성전자" in msg
    assert "http://x" in msg


def test_per_report_message_prefers_pdf_url_over_read_url():
    # 모바일에서 목록으로 튀는 read_url 대신 PDF 원본을 링크해야 한다
    r = Report(
        category="company",
        title="t",
        broker="삼성증권",
        date="26.07.07",
        views=1,
        read_url="http://naver/read",
        pdf_url="http://naver/report.pdf",
    )
    r.summary = "요약"
    msg = pipeline._format_report_message(r)
    assert "http://naver/report.pdf" in msg
    assert "http://naver/read" not in msg


@pytest.fixture
def stub_digest(monkeypatch):
    """카테고리 종합(digest) 경로의 크롤·요약·종합·발송을 스텁으로 대체한다."""

    def _apply(*, crawled, digest: DigestResult):
        monkeypatch.setattr(pipeline, "crawl_categories", lambda cats, target_date=None: crawled)
        monkeypatch.setattr(pipeline, "enrich_with_text", lambda reports: crawled)
        monkeypatch.setattr(pipeline, "OllamaClient", lambda host, key: object())
        monkeypatch.setattr(pipeline.analyzer, "summarize_reports", lambda c, m, reports: crawled)
        monkeypatch.setattr(pipeline.analyzer, "synthesize_digest", lambda c, m, reports: digest)
        monkeypatch.setattr(pipeline, "UrlShortener", lambda *a, **k: _NoopShortener())
        sent = []
        monkeypatch.setattr(pipeline, "TelegramSender", lambda t, c: _RecordingSender(sent))
        return sent

    return _apply


class _NoopShortener:
    def shorten(self, url: str) -> str:
        return url


def test_digest_archives_with_kind_and_sources(stub_digest, tmp_path):
    src = Report(category="invest", title="투자전략", broker="KB", date="26.07.09", views=9,
                 stock_code="005930", read_url="http://x")
    digest = DigestResult(text="투자 종합 본문", category="invest", report_count=1, sources=[src])
    sent = stub_digest(crawled=[src], digest=digest)
    config = _config(tmp_path)

    result = pipeline.run_category_digest(config, "invest")

    assert result is not None
    assert sent == [result]
    entries = _spool_entries(config)
    assert len(entries) == 1
    assert entries[0]["kind"] == "digest_invest"
    assert entries[0]["stock_codes"] == ["005930"]
    assert len(entries[0]["source_refs"]["reports"]) == 1


def test_closing_digest_archives_as_closing_kind(stub_digest, tmp_path):
    src = Report(category="market_info", title="마감시황", broker="A", date="26.07.09", views=1)
    digest = DigestResult(text="마감 본문", category="market_info", report_count=1, sources=[src])
    stub_digest(crawled=[src], digest=digest)
    config = _config(tmp_path)

    pipeline.run_category_digest(config, "market_info", closing=True)

    entries = _spool_entries(config)
    assert entries[0]["kind"] == "closing"


@pytest.fixture
def stub_entity(monkeypatch):
    """종목/산업 단위 종합(per-entity) 경로를 스텁으로 대체한다."""

    def _apply(*, crawled):
        monkeypatch.setattr(pipeline, "crawl_categories", lambda cats, target_date=None: crawled)
        monkeypatch.setattr(pipeline, "enrich_with_text", lambda reports: crawled)
        monkeypatch.setattr(pipeline, "OllamaClient", lambda host, key: object())
        monkeypatch.setattr(pipeline.analyzer, "summarize_reports", lambda c, m, reports: crawled)
        monkeypatch.setattr(pipeline.analyzer, "synthesize_entity", lambda c, m, group: "종합요약")
        monkeypatch.setattr(pipeline, "UrlShortener", lambda *a, **k: _NoopShortener())
        sent = []
        monkeypatch.setattr(pipeline, "TelegramSender", lambda t, c: _RecordingSender(sent))
        return sent

    return _apply


def test_per_entity_archives_per_group_with_tags(stub_entity, tmp_path):
    reports = [
        _linked_report("삼성 리포트", 100, "요약", "http://a", stock="삼성전자"),
        _linked_report("하이닉스 리포트", 50, "요약", "http://b", stock="SK하이닉스"),
    ]
    reports[0].stock_code = "005930"
    reports[1].stock_code = "000660"
    stub_entity(crawled=reports)
    config = _config(tmp_path)

    count = pipeline.run_per_entity_briefing(config, ["company"])

    assert count == 2
    entries = _spool_entries(config)
    assert len(entries) == 2
    assert all(e["kind"] == "per_entity" for e in entries)
    all_codes = {c for e in entries for c in e["stock_codes"]}
    assert all_codes == {"005930", "000660"}
