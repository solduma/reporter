"""DartDisclosureAdapter — KrDisclosurePort 를 DART client 모듈로 구현.

api_key 를 어댑터가 쥐고, 포트 메서드는 연산 인자만 받는다(자격증명은 구현이 캡슐화).
"""

from __future__ import annotations

from datetime import date

import requests

from app.adapters.dart import client


class DartDisclosureAdapter:
    """KrDisclosurePort 구현. 모든 DART 공시 조회를 이 어댑터로 격리한다."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_disclosures(
        self, corp_code: str, stock_code: str, begin: date, end: date, session: requests.Session
    ) -> list[client.Disclosure]:
        return client.fetch_disclosures(self._api_key, corp_code, stock_code, begin, end, session)

    def fetch_document_text(
        self, rcept_no: str, session: requests.Session, max_chars: int = 6000
    ) -> str:
        return client.fetch_document_text(self._api_key, rcept_no, session, max_chars)

    def find_periodic_report(
        self, corp_code: str, year: int, kind: str, session: requests.Session
    ) -> str | None:
        return client.find_periodic_report(self._api_key, corp_code, year, kind, session)

    def fetch_ownership_changes(
        self, corp_code: str, session: requests.Session
    ) -> dict[str, client.OwnershipChange]:
        return client.fetch_ownership_changes(self._api_key, corp_code, session)
