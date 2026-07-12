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


@dataclass
class OwnershipChange:
    """임원·주요주주 특정증권 소유 변동 1건(DART elestock.json).

    소유상황보고서 원문의 표를 태그 제거로 뭉개면 방향을 못 읽어 HOLD 로 오분류된다.
    구조화 API 로 부호있는 증감(shares_delta)·수량을 확보하고, 사유(reason)는 문서에서 보강한다.
    """

    reporter: str  # 보고자
    position: str  # 직위(isu_exctv_ofcps)
    is_registered: str  # 등기임원 여부(isu_exctv_rgist_at)
    shares_after: int  # 변동후 소유수량
    shares_delta: int  # 증감(+취득 / -처분)
    reason: str = ""  # 사유(장내매수/장내매도/증여 등) — 문서 텍스트에서 보강


def summarize_ownership(change: OwnershipChange) -> str:
    """소유변동 구조화 데이터 → LLM 프롬프트용 한국어 요약. 방향·수량·사유를 명시한다.

    +증감은 취득(매수 성격), -증감은 처분(매도·증여 등)이다. 사유가 방향의 의미를 가른다
    (장내매수=신뢰 신호, 증여·상속=지분 이동으로 시장 신호 아님)—판단은 LLM 에 맡긴다.
    """
    verb = "취득" if change.shares_delta >= 0 else "처분"
    reg = f", {change.is_registered}" if change.is_registered else ""
    reason = f"\n변동사유: {change.reason}" if change.reason else ""
    return (
        f"보고자: {change.reporter} ({change.position}{reg})\n"
        f"소유 증감: {change.shares_delta:+,}주 {verb} "
        f"(변동후 {change.shares_after:,}주 보유){reason}"
    )
