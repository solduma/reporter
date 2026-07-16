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
    # 경제 캘린더 — FRED(미국 매크로 발표일·실적치, 무료 키). 미설정 시 수동 고정일정만.
    fred_api_key: str = ""
    # 딥다이브 웹 검색 — 네이버 검색 API(블로그·뉴스, 일 25,000회 무료). 헤더 인증 2개. 미설정 시 웹검색 비활성.
    naver_client_id: str = ""
    naver_client_secret: str = ""

    # SEC EDGAR(US 재무) — 무인증이나 연락처 명시 User-Agent 필수(SEC 정책). 기본값은 일반 표기.
    sec_user_agent: str = "reporter research contact@example.com"

    # 차트 폴백 소스: 네이버 실패 시 KIS(한국투자증권 OpenAPI)·KRX 로 국내 봉 조회.
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    krx_api: str = ""

    # CLI 텔레그램 발송이 남기는 브로드캐스트 스풀 경로(비우면 repo logs/broadcasts.jsonl)
    broadcast_spool: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
