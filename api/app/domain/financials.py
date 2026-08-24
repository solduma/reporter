"""KR 재무 TTM·분기환산 순수 도메인 규칙 — DART fnlttSinglAcntAll 회계 관례.

DART `thstrm_amount` 는 실측상 **1~3Q(분기·반기보고서)는 당기 3개월 개별값, 4Q(사업보고서)는
연간 누적**이다(삼성·현대차·SK 등 다종목 실조회로 확정, 2026-07). 따라서 Q4 개별 = 연간 -
(Q1+Q2+Q3), 1~3Q 는 그대로.

financials_backfill(PER/PBR/PSR 10년 백필)이 이 규칙으로 분기 개별값을 환산한 뒤(음수-매출
필터 적용) 연속 4분기를 합해 TTM 을 만든다. 순수 함수(IO 없음). 입력은 {(year, quarter): value}
원자료(raw), 반환은 float|None.
"""

from __future__ import annotations

YearQuarter = tuple[int, int]


def prev_yq(yq: YearQuarter) -> YearQuarter:
    """직전 분기. Q1 이전은 전년 Q4."""
    year, q = yq
    return (year - 1, 4) if q == 1 else (year, q - 1)


def ttm_from_discrete(discrete: dict[YearQuarter, float | None], yq: YearQuarter) -> float | None:
    """이미 분기 개별 환산된 dict 에서 yq 포함 연속 4개 분기 합(TTM). 결측·불연속이면 None.

    환산(1~3Q 그대로·Q4=연간-누적)은 discrete_quarter 로 끝난 뒤의 discrete dict 를 받는다.
    annual fallback 은 호출자(_ttm_value) 어댑터가 rawdict 에서 직접 처리한다.
    """
    total = 0.0
    cursor = yq
    for _ in range(4):
        v = discrete.get(cursor)
        if v is None:
            return None
        total += v
        cursor = prev_yq(cursor)
    return total


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
    # Q4: 연간 - (Q1+Q2+Q3). 일부 분기 누락 시(반기누적 등) None 반환.
    parts = [raw.get((year, i)) for i in (1, 2, 3)]
    if any(p is None for p in parts):
        return None  # 분기 누락 시 discrete 환산 불가 — _ttm_value 어댑터가 annual fallback
    return val - sum(parts)
