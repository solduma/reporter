"""API 설정. APP_ENV 로 .env.{APP_ENV} 를 선택한다 (기존 reporter 규칙과 동일)."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_ENV = os.getenv("APP_ENV", "dev")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{_APP_ENV}"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 인프라 (docker-compose 격리 포트). 호스트 개발 기본값 = localhost 매핑 포트.
    postgres_url: str = "postgresql+psycopg://reporter:reporter@localhost:5433/reporter"
    redis_url: str = "redis://localhost:6380/0"

    minio_endpoint: str = "localhost:9010"
    minio_access_key: str = "reporter"
    minio_secret_key: str = "reporter-secret"
    minio_bucket: str = "reporter-pdfs"
    minio_secure: bool = False

    # GLM (기존 reporter 와 동일 키 사용)
    ollama_host: str = "https://ollama.com"
    ollama_api_key: str = ""
    summary_model: str = "glm-5.2:cloud"
    insight_model: str = "glm-5.2:cloud"

    # 크롤 대상 (Today's Brew 는 종목/산업 + 시황)
    report_categories: tuple[str, ...] = ("company", "industry")

    # 외부 데이터 소스 키 (7단계 DART 공시 / 4단계 관세청 무역통계)
    dart_api_key: str = ""
    customs_api_key: str = ""

    # CLI 텔레그램 발송이 남기는 브로드캐스트 스풀 경로(비우면 repo logs/broadcasts.jsonl)
    broadcast_spool: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
