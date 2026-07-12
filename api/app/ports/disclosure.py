"""DisclosurePort — 공시 조회 인터페이스(시장별 초점 포트).

DART(KR)와 SEC(US)는 데이터 모양·인증·소비자가 완전히 달라 하나로 강제 통합하면 leaky 하다.
그래서 각 수집 서비스가 실제로 쓰는 연산만 담은 **두 개의 초점 포트**로 나눈다. 자격증명(api_key·
settings)은 구현(adapters/dart·sec)이 캡슐화하고, 포트 메서드는 연산 인자만 받는다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol

import requests

if TYPE_CHECKING:  # 런타임 결합 없음 — 도메인 값객체를 반환 타입 힌트로만 참조.
    from app.domain.disclosure import Disclosure, Filing, OwnershipChange


class KrDisclosurePort(Protocol):
    """DART 공시 조회 — dart_ingest(공시 수집)·report_ingest(정기보고서) 가 의존."""

    def fetch_disclosures(
        self, corp_code: str, stock_code: str, begin: date, end: date, session: requests.Session
    ) -> list[Disclosure]:
        """corp_code + 기간의 공시 목록(페이지네이션 처리)."""
        ...

    def fetch_document_text(
        self, rcept_no: str, session: requests.Session, max_chars: int = 6000
    ) -> str:
        """공시 원문(document.xml)에서 태그를 벗긴 앞 max_chars 텍스트. 실패 시 빈 문자열."""
        ...

    def find_periodic_report(
        self, corp_code: str, year: int, kind: str, session: requests.Session
    ) -> str | None:
        """해당 회계연도 정기공시(annual|half|quarter)의 접수번호. 없으면 None."""
        ...

    def fetch_ownership_changes(
        self, corp_code: str, session: requests.Session
    ) -> dict[str, OwnershipChange]:
        """임원·주요주주 소유변동(elestock) → {rcept_no: 변동}. 방향·수량 확보. 실패 시 빈 dict."""
        ...


class UsDisclosurePort(Protocol):
    """SEC 공시 조회 — us_disclosure_ingest(8-K 수집) 가 의존."""

    def resolve_cik(self, ticker: str, session: requests.Session | None = None) -> int | None:
        """ticker(대소문자 무관) → CIK. 없으면 None."""
        ...

    def fetch_recent_filings(
        self,
        cik: int,
        forms: tuple[str, ...] = ("8-K",),
        limit: int = 20,
        session: requests.Session | None = None,
    ) -> list[Filing]:
        """CIK 의 최근 공시 목록(기본 8-K). 실패 시 빈 리스트."""
        ...

    def describe_8k_items(self, items: str) -> str:
        """8-K item 코드 문자열 → 사람이 읽는 한글 요약(LLM 없이)."""
        ...
