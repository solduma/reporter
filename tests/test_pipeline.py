from pathlib import Path

import pytest

import reporter.pipeline as pipeline
from reporter.config import Config
from reporter.models import Briefing, Report


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
    sent = stub_pipeline(crawled=[], enriched=[], summarized=[])
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
