"""시황(market_info) 리포트 분류 — 국내 마감시황 판별.

네이버 시황 리스트는 '발행일' 기준이라, 오늘 발행분에 '전일 국내 장 마감 리뷰'
리포트가 섞인다(예: 7/9 발행 `국내주식 마감 시황 (26.07.08)`). 오전 브리핑은
오늘 전망이 목적이므로 국내 마감시황을 제외하고, 17시 마감 브리핑은 그것만 모은다.

주의: '미국 주식시장 마감 시황'(간밤 미국장)은 오늘 아침에 유효한 정보이므로
국내 마감시황이 아니다 — '마감' 문자열 일괄 제외를 하면 안 된다.
"""

from __future__ import annotations

import re

from .models import Report

# '마감' 을 실제 장 마감 맥락으로만 인식한다. `테마 감소`·`드라마 감독` 같은 오탐을 막으려고
# 앞에 한글이 붙지 않는 '마감'만, 그리고 '장마감' 형태를 허용한다.
_CLOSE_WORD = re.compile(r"(?<![가-힣])마감|장\s*마감")
# 국내 시장 지시어(이게 있으면 해외 언급이 있어도 국내 마감으로 본다).
_DOMESTIC = re.compile(r"국내|코스피|코스닥|KOSPI|KOSDAQ|국장")
# 해외 장이 '마감의 주체'인 제목(예: `미국 주식시장 마감 시황`, `뉴욕증시 마감`).
_OVERSEAS_CLOSE = re.compile(
    r"(미국|뉴욕|나스닥|다우|S&P|해외|글로벌)\s*(증시|주식시장|시장)?\s*마감"
)


def is_domestic_closing(report: Report) -> bool:
    """국내 장 '마감시황' 리포트인지 판별한다(전일 리뷰 성격).

    규칙(국내 신호 우선):
    - 제목에 실제 마감 맥락('마감'/'장마감')이 없으면 False.
    - 국내 지시어(국내·코스피·코스닥…)가 있으면 → 국내 마감(True). 해외 언급이 섞여도
      (예: `뉴욕發 훈풍에 코스피 마감 강세`) 국내 마감으로 본다.
    - 국내 지시어가 없고 해외 장이 마감 주체이면(`미국 주식시장 마감 시황`) → False(오늘 아침 유효).
    - 둘 다 없으면(브로커 관용구 `장마감코멘트` 등) 국내 마감으로 본다(True).
    """
    title = report.title or ""
    if not _CLOSE_WORD.search(title):
        return False
    if _DOMESTIC.search(title):
        return True
    # 국내 지시어가 없을 때만: 해외 장이 마감 주체이면 제외, 아니면(브로커 관용구) 국내로 본다.
    return not _OVERSEAS_CLOSE.search(title)


def split_by_closing(reports: list[Report]) -> tuple[list[Report], list[Report]]:
    """(오전용=국내마감 제외, 마감용=국내마감만) 으로 나눈다."""
    morning: list[Report] = []
    closing: list[Report] = []
    for r in reports:
        (closing if is_domestic_closing(r) else morning).append(r)
    return morning, closing
