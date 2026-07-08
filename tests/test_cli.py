from pathlib import Path

import pytest

import reporter.cli as cli
from reporter.config import Config


def _config(tmp_path: Path, **overrides) -> Config:
    base = {
        "ollama_host": "https://ollama.com",
        "ollama_api_key": "key",
        "summary_model": "glm-5.2:cloud",
        "insight_model": "glm-5.2:cloud",
        "telegram_bot_token": "token",
        "telegram_chat_id": "123",
        "root": tmp_path,
    }
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def patch_config(monkeypatch):
    def _apply(config: Config):
        monkeypatch.setattr(cli, "load_config", lambda: config)

    return _apply


def test_afternoon_aborts_when_ollama_key_missing(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, ollama_api_key=""))
    called = False

    def _should_not_run(config):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "run_afternoon_research", _should_not_run)

    assert cli.main(["--afternoon"]) == 2
    assert called is False


def test_morning_aborts_when_telegram_missing(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, telegram_chat_id=""))
    called = False

    def _should_not_run(*a, **k):
        nonlocal called
        called = True
        return "x"

    monkeypatch.setattr(cli, "run_morning_briefing", _should_not_run)

    assert cli.main(["--batch", "1"]) == 2
    assert called is False


def test_morning_runs_when_config_complete(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}

    def _fake_run(config, categories, top_n):
        seen["categories"] = categories
        seen["top_n"] = top_n
        return "briefing"

    monkeypatch.setattr(cli, "run_morning_briefing", _fake_run)

    assert cli.main(["--batch", "1", "--top-n", "3"]) == 0
    assert seen["top_n"] == 3
    assert seen["categories"] == ["company", "industry"]


def test_chat_id_only_requires_bot_token(monkeypatch, patch_config, tmp_path):
    # chat_id 조회는 아직 chat_id 를 모를 때 쓰므로 bot token 만 있으면 통과해야 한다
    patch_config(_config(tmp_path, telegram_chat_id="", ollama_api_key=""))
    monkeypatch.setattr(cli, "resolve_chat_ids", lambda token: [(42, "테스터")])

    assert cli.main(["--chat-id"]) == 0


def test_chat_id_aborts_without_bot_token(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, telegram_bot_token=""))
    called = False

    def _should_not_run(token):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(cli, "resolve_chat_ids", _should_not_run)

    assert cli.main(["--chat-id"]) == 2
    assert called is False


def test_per_report_dispatches_with_date_and_categories(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}

    def _fake_run(config, categories, target_date):
        seen["categories"] = categories
        seen["target_date"] = target_date
        return 3

    monkeypatch.setattr(cli, "run_per_report_briefing", _fake_run)

    assert cli.main(["--per-report", "1", "--date", "26.07.07"]) == 0
    assert seen["categories"] == ["company", "industry"]
    assert seen["target_date"] == "26.07.07"


def test_per_report_rejects_malformed_date(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    called = False

    def _should_not_run(*a, **k):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "run_per_report_briefing", _should_not_run)

    # 잘못된 날짜 포맷은 argparse 단계에서 거부되어 SystemExit(2), 실행 안 됨
    with pytest.raises(SystemExit) as exc:
        cli.main(["--per-report", "1", "--date", "2026-07-07"])
    assert exc.value.code == 2
    assert called is False


def test_per_report_aborts_when_env_missing(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, ollama_api_key=""))
    called = False

    def _should_not_run(*a, **k):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "run_per_report_briefing", _should_not_run)

    assert cli.main(["--per-report", "1"]) == 2
    assert called is False


def test_reset_log_needs_no_env(monkeypatch, patch_config, tmp_path):
    # 모든 시크릿이 비어도 로그 초기화는 동작해야 한다
    patch_config(_config(tmp_path, ollama_api_key="", telegram_bot_token="", telegram_chat_id=""))

    assert cli.main(["--reset-log"]) == 0
    assert (tmp_path / "logs" / "today_briefing.txt").read_text() == ""


def test_digest_dispatches_with_category(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}
    monkeypatch.setattr(cli, "run_category_digest", lambda c, cat: seen.setdefault("cat", cat) or "msg")
    assert cli.main(["--digest", "market_info"]) == 0
    assert seen["cat"] == "market_info"


def test_closing_dispatches_market_info_closing(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}

    def _fake(config, category, closing=False):
        seen["category"] = category
        seen["closing"] = closing
        return "msg"

    monkeypatch.setattr(cli, "run_category_digest", _fake)
    assert cli.main(["--closing"]) == 0
    assert seen == {"category": "market_info", "closing": True}


def test_per_entity_dispatches_batch1(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}

    def _fake(config, categories, target_date=None):
        seen["categories"] = categories
        return 2

    monkeypatch.setattr(cli, "run_per_entity_briefing", _fake)
    assert cli.main(["--per-entity"]) == 0
    assert seen["categories"] == ["company", "industry"]


def test_news_runs_with_full_env(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    monkeypatch.setattr(cli, "run_market_news", lambda c: 1)
    assert cli.main(["--news"]) == 0


def test_news_aborts_when_ollama_missing(monkeypatch, patch_config, tmp_path):
    # 뉴스도 GLM 종합을 쓰므로 ollama 키 없으면 크래시 대신 조기 종료(exit 2)
    patch_config(_config(tmp_path, ollama_api_key=""))
    called = False

    def _should_not_run(c):
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr(cli, "run_market_news", _should_not_run)
    assert cli.main(["--news"]) == 2
    assert called is False


def test_news_aborts_without_telegram(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, telegram_bot_token=""))
    called = False

    def _should_not_run(c):
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr(cli, "run_market_news", _should_not_run)
    assert cli.main(["--news"]) == 2
    assert called is False


def test_premarket_dispatches(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path))
    seen = {}
    monkeypatch.setattr(cli, "run_premarket", lambda c: seen.setdefault("ran", True) or 1)
    assert cli.main(["--premarket"]) == 0
    assert seen["ran"] is True


def test_premarket_aborts_when_ollama_missing(monkeypatch, patch_config, tmp_path):
    patch_config(_config(tmp_path, ollama_api_key=""))
    called = False

    def _should_not_run(c):
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr(cli, "run_premarket", _should_not_run)
    assert cli.main(["--premarket"]) == 2
    assert called is False
