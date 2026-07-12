"""공시 값객체(DTO) — 순수 데이터. 어댑터(dart·sec)가 채우고, 포트가 반환 타입으로 참조한다.

adapters/dart·sec 에 있던 Disclosure·Filing 을 도메인으로 올려, 포트(app.ports.disclosure)가
어댑터 내부 타입에 의존하지 않게 한다(ports-leaf 계약: ports 는 adapters 를 모른다). 어댑터
client 는 하위호환을 위해 이 타입을 재노출한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Disclosure:
    """DART 공시 1건(목록 조회 결과)."""

    rcept_no: str
    corp_code: str
    stock_code: str
    report_nm: str
    flr_nm: str
    rcept_dt: date
    dart_url: str


@dataclass
class Filing:
    """SEC EDGAR 공시(제출서류) 1건."""

    accession: str
    form: str  # 8-K | 10-K ...
    filing_date: str  # YYYY-MM-DD
    items: str  # 8-K item 코드(예 '5.02,7.01'), 없으면 ''
    primary_doc_url: str
