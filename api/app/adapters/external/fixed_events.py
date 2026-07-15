"""수동 고정 일정 시드 — 무료 자동 소스가 없거나 과한 이벤트(FOMC·선거·금통위 등).

이런 이벤트는 1~2년 전 공표돼 거의 안 바뀌므로 수집 파이프라인보다 하드코딩이 실용적이다.
날짜가 지나면 자동으로 '과거 이벤트'로 취급된다(별도 갱신 불요). 새 연도 일정은 여기 추가.
출처: Fed FOMC 캘린더, 미국 선거법(11월 첫 월요일 다음 화요일), 한국은행 금통위 일정.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class FixedEvent:
    event_date: date
    title: str
    region: str
    kind: str  # fomc | election | macro | geo
    importance: int


# (YYYY-MM-DD, 제목, region, kind, importance). 알려진 미래 + 최근 과거 일정.
_SEED: list[tuple[str, str, str, str, int]] = [
    # 미국 FOMC(금리 결정) — 2026년 일정.
    ("2026-01-28", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-03-18", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-04-29", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-06-17", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-07-29", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-09-16", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-10-28", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    ("2026-12-09", "FOMC 회의 (금리 결정)", "US", "fomc", 3),
    # 한국은행 금융통화위원회(기준금리) — 2026년 통방 결정회의.
    ("2026-01-15", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-02-26", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-04-16", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-05-28", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-07-16", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-08-27", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-10-15", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    ("2026-11-26", "한국은행 금통위 (기준금리)", "KR", "macro", 3),
    # 지정학·중대일.
    ("2026-11-03", "미국 중간선거", "US", "election", 3),
    ("2028-11-07", "미국 대통령 선거", "US", "election", 3),
]


def fetch_fixed(start: date, end: date) -> list[FixedEvent]:
    """[start, end] 구간의 고정 일정. source_key 는 날짜+제목으로 멱등."""
    out: list[FixedEvent] = []
    for raw, title, region, kind, importance in _SEED:
        d = date.fromisoformat(raw)
        if start <= d <= end:
            out.append(FixedEvent(d, title, region, kind, importance))
    return out
