"""환경변수 로딩 및 설정. APP_ENV 로 .env.{APP_ENV} 를 선택한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    app_env = os.getenv("APP_ENV", "dev")
    # override=False: 이미 export 된 정상 값을 만료된 .env 값이 덮어쓰지 않도록 한다.
    load_dotenv(_ROOT / f".env.{app_env}", override=False)
    load_dotenv(_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Config:
    ollama_host: str
    ollama_api_key: str
    summary_model: str
    insight_model: str
    telegram_bot_token: str
    telegram_chat_id: str
    root: Path = field(default=_ROOT)

    @property
    def logs_dir(self) -> Path:
        d = self.root / "logs"
        d.mkdir(exist_ok=True)
        return d


def load_config() -> Config:
    _load_env()
    return Config(
        ollama_host=os.getenv("OLLAMA_HOST", "https://ollama.com").rstrip("/"),
        ollama_api_key=os.getenv("OLLAMA_API_KEY", ""),
        summary_model=os.getenv("OLLAMA_SUMMARY_MODEL", "glm-5.2:cloud"),
        insight_model=os.getenv("OLLAMA_INSIGHT_MODEL", "glm-5.2:cloud"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
