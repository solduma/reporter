from pathlib import Path

import pytest

import reporter.afternoon as afternoon
from reporter.afternoon import _extract_keywords, run_afternoon_research
from reporter.config import Config
from reporter.news import NewsItem


class _FakeClient:
    def __init__(self, reply: str):
        self._reply = reply

    def chat(self, model, system, user, temperature=0.3):
        return self._reply


def test_strips_list_markers_but_keeps_digit_leading_keywords():
    # LLM 이 번호/불릿을 붙여도 마커만 제거하고, 숫자로 시작하는 종목/테마는 보존해야 한다
    reply = "1. 2차전지\n2) 5G\n- 4대금융지주\n• 삼성전자\n3분기 실적"
    keywords = _extract_keywords(_FakeClient(reply), "m", "briefing")
    assert keywords == ["2차전지", "5G", "4대금융지주", "삼성전자", "3분기 실적"]


def test_limits_to_five_keywords():
    reply = "\n".join(f"종목{i}" for i in range(10))
    keywords = _extract_keywords(_FakeClient(reply), "m", "briefing")
    assert len(keywords) == 5


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


class _RecordingSender:
    def __init__(self, sink: list):
        self._sink = sink

    def send(self, message: str) -> int:
        self._sink.append(message)
        return 1


class _ScriptedClient:
    """키워드 추출은 고정 응답, 업데이트 분석은 keyword→응답 매핑. 예외 주입 가능."""

    def __init__(self, keywords_reply: str, analyses: dict):
        self._keywords_reply = keywords_reply
        self._analyses = analyses
        self.calls = 0

    def chat(self, model, system, user, temperature=0.3):
        self.calls += 1
        if self.calls == 1:  # 첫 호출은 키워드 추출
            return self._keywords_reply
        for keyword, value in self._analyses.items():
            if keyword in user:
                if isinstance(value, Exception):
                    raise value
                return value
        return "기본 분석"


@pytest.fixture
def wire_afternoon(monkeypatch):
    def _apply(*, client, news_by_keyword):
        monkeypatch.setattr(afternoon, "OllamaClient", lambda host, key: client)
        monkeypatch.setattr(
            afternoon.news, "search", lambda kw, limit, session: news_by_keyword.get(kw, [])
        )
        sent = []
        monkeypatch.setattr(
            afternoon, "TelegramSender", lambda token, chat_id: _RecordingSender(sent)
        )
        return sent

    return _apply


def test_returns_zero_without_briefing_log(tmp_path):
    # 브리핑 로그가 없으면 예외 없이 0 을 반환해야 한다
    assert run_afternoon_research(_config(tmp_path)) == 0


def _write_briefing(config: Config, text: str = "오전 브리핑") -> None:
    (config.logs_dir / "today_briefing.txt").write_text(text, encoding="utf-8")


def test_skips_keywords_without_news(wire_afternoon, tmp_path):
    config = _config(tmp_path)
    _write_briefing(config)
    item = NewsItem(title="헤드라인", source="연합뉴스", link="http://x")
    client = _ScriptedClient("삼성전자\nSK하이닉스", {"삼성전자": "분석"})
    sent = wire_afternoon(client=client, news_by_keyword={"삼성전자": [item]})

    # SK하이닉스는 뉴스가 없어 발송에서 제외 → 1건만 발송
    assert run_afternoon_research(config) == 1
    assert len(sent) == 1
    assert "삼성전자" in sent[0]


def test_analysis_failure_skips_only_that_keyword(wire_afternoon, tmp_path):
    config = _config(tmp_path)
    _write_briefing(config)
    item = NewsItem(title="헤드라인", source="연합뉴스", link="http://x")
    client = _ScriptedClient(
        "삼성전자\nSK하이닉스",
        {"삼성전자": RuntimeError("LLM down"), "SK하이닉스": "정상 분석"},
    )
    sent = wire_afternoon(
        client=client, news_by_keyword={"삼성전자": [item], "SK하이닉스": [item]}
    )

    # 삼성전자 분석은 실패해도 루프가 멈추지 않고 SK하이닉스는 발송된다
    assert run_afternoon_research(config) == 1
    assert len(sent) == 1
    assert "SK하이닉스" in sent[0]


def test_message_includes_article_links(wire_afternoon, tmp_path):
    config = _config(tmp_path)
    _write_briefing(config)
    items = [
        NewsItem(title="h1", source="연합뉴스", link="http://a"),
        NewsItem(title="h2", source="한국경제", link="http://b"),
    ]
    client = _ScriptedClient("삼성전자", {"삼성전자": "분석 결과"})
    sent = wire_afternoon(client=client, news_by_keyword={"삼성전자": items})

    run_afternoon_research(config)
    # 출처 이름뿐 아니라 실제 기사 페이지 링크가 메시지에 포함돼야 한다
    assert "연합뉴스" in sent[0] and "한국경제" in sent[0]
    assert "http://a" in sent[0] and "http://b" in sent[0]
