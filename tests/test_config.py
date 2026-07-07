from pathlib import Path

from reporter.config import Config


def _config(**overrides) -> Config:
    base = {
        "ollama_host": "https://ollama.com",
        "ollama_api_key": "key",
        "summary_model": "glm-5.2:cloud",
        "insight_model": "glm-5.2:cloud",
        "telegram_bot_token": "token",
        "telegram_chat_id": "123",
        "root": Path("/tmp"),
    }
    base.update(overrides)
    return Config(**base)


def test_missing_returns_env_names_for_empty_fields():
    config = _config(ollama_api_key="", telegram_chat_id="")
    assert config.missing("ollama_api_key", "telegram_chat_id") == [
        "OLLAMA_API_KEY",
        "TELEGRAM_CHAT_ID",
    ]


def test_missing_returns_empty_when_all_present():
    config = _config()
    assert config.missing("ollama_api_key", "telegram_bot_token", "telegram_chat_id") == []


def test_missing_only_reports_requested_fields():
    # ollama 키가 비어도 텔레그램만 검증하면 걸리지 않는다
    config = _config(ollama_api_key="")
    assert config.missing("telegram_bot_token", "telegram_chat_id") == []
