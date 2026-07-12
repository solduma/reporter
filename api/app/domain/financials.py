"""KR 재무 TTM·분기환산 순수 도메인 규칙 — DART fnlttSinglAcntAll 회계 관례.

DART `thstrm_amount` 는 실측상 **1~3Q(분기·반기보고서)는 당기 3개월 개별값, 4Q(사업보고서)는
연간 누적**이다(삼성·현대차·SK 등 다종목 실조회로 확정, 2026-07). 따라서 Q4 개별 = 연간 -
(Q1+Q2+Q3), 1~3Q 는 그대로. TTM 은 연속 4개 분기 개별값 합.

이 규칙이 valuation_ingest(EV/EBITDA)와 financials_backfill(PER/PBR/PSR) 양쪽에 중복·상충
(valuation 은 잘못 '전분기 YTD 누적'으로 가정)했던 것을 한 곳으로 통일한다. 순수 함수(IO 없음).
입력은 {(year, quarter): value} 원자료(raw), 반환은 float|None.
"""

from __future__ import annotations

YearQuarter = tuple[int, int]


def prev_yq(yq: YearQuarter) -> YearQuarter:
    """직전 분기. Q1 이전은 전년 Q4."""
    year, q = yq
    return (year - 1, 4) if q == 1 else (year, q - 1)


def discrete_quarter(raw: dict[YearQuarter, float | None], yq: YearQuarter) -> float | None:
    """DART 원자료를 분기 개별값으로 환산. 1~3Q 는 그대로, Q4 = 연간 - (Q1+Q2+Q3).

    일부 회사가 반기보고서를 누적으로 내면 Q4 환산이 음수가 될 수 있다 — 매출처럼 음수 불가
    항목은 호출측이 거른다(여기선 부호를 판단하지 않고 산술만 한다).
    """
    year, q = yq
    val = raw.get(yq)
    if val is None:
        return None
    if q != 4:
        return val
    parts = [raw.get((year, i)) for i in (1, 2, 3)]
    if any(p is None for p in parts):
        return None
    return val - sum(parts)


def ttm(raw: dict[YearQuarter, float | None], yq: YearQuarter) -> float | None:
    """yq 포함 **연속 4개 분기** 개별값 합(TTM). 하나라도 결측·불연속이면 None."""
    total = 0.0
    cursor = yq
    for _ in range(4):
        v = discrete_quarter(raw, cursor)
        if v is None:
            return None
        total += v
        cursor = prev_yq(cursor)
    return total
