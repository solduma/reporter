"""SecDisclosureAdapter — UsDisclosurePort 를 SEC client 모듈로 구현.

settings(UA·throttle 설정)를 어댑터가 쥐고, 포트 메서드는 연산 인자만 받는다.
"""

from __future__ import annotations

import requests

from app.adapters.sec import client
from app.config import Settings


class SecDisclosureAdapter:
    """UsDisclosurePort 구현. SEC 공시 조회를 이 어댑터로 격리한다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve_cik(self, ticker: str, session: requests.Session | None = None) -> int | None:
        return client.resolve_cik(self._settings, ticker, session)

    def fetch_recent_filings(
        self,
        cik: int,
        forms: tuple[str, ...] = ("8-K",),
        limit: int = 20,
        session: requests.Session | None = None,
    ) -> list[client.Filing]:
        return client.fetch_recent_filings(self._settings, cik, forms, limit, session)

    def describe_8k_items(self, items: str) -> str:
        return client.describe_8k_items(items)
