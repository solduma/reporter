"""FRED (Federal Reserve Economic Data) — 미국 매크로 지표 발표일 + 실적치.

무료 API 키(fredaccount.stlouisfed.org/apikeys). 두 엔드포인트만 쓴다:
- release/dates: 릴리스별 발표일(과거+미래). include_release_dates_with_no_data=true 로
  아직 데이터 없는 **미래 예정일**까지 받는다.
- series/observations: 시계열 관측치(발표된 실적/직전치). consensus(예상치)는 FRED 에 없다.

FRED 는 발표일과 값을 별도로 준다 → 값은 최신 관측치를 발표일에 근사 매핑한다(월간 지표라
발표월 전월분이 대개 최신치). key 미설정/실패 시 빈 리스트(graceful degrade).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.stlouisfed.org/fred"

# 지수 영향 큰 미국 매크로 릴리스. (release_id, 표시명, 대표 series_id, 중요도, 단위).
# series_id 는 헤드라인 지표 하나를 대표로 — 값 표기는 참고용.
RELEASES: list[tuple[int, str, str, int, str]] = [
    (10, "미국 CPI", "CPIAUCSL", 3, ""),          # 소비자물가지수(레벨)
    (50, "미국 고용보고서", "PAYEMS", 3, "K"),      # 비농업 고용(NFP)
    (53, "미국 GDP", "GDPC1", 3, ""),              # 실질 GDP
    (54, "미국 PCE 물가", "PCEPI", 2, ""),          # PCE 물가지수
    (21, "미국 개인소득·지출", "PCE", 2, ""),        # 개인소비지출
]


@dataclass
class FredEvent:
    event_date: date
    title: str
    release_id: int
    series_id: str
    importance: int
    latest_value: str | None  # 최신 관측치(발표된 실적, 문자열 원표기)
    prev_value: str | None    # 직전 관측치


def _get(path: str, key: str, **params) -> dict | None:
    params.update(api_key=key, file_type="json")
    try:
        resp = requests.get(f"{_BASE}/{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("FRED %s failed: %s", path, e)
        return None


def _latest_two_observations(key: str, series_id: str) -> tuple[str | None, str | None]:
    """시계열 최신 2개 관측치(최신, 직전). 실패 시 (None, None)."""
    data = _get(
        "series/observations", key, series_id=series_id,
        sort_order="desc", limit=2,
    )
    if not data:
        return None, None
    obs = data.get("observations", [])
    vals = [o.get("value") for o in obs if o.get("value") not in (None, ".", "")]
    latest = vals[0] if len(vals) >= 1 else None
    prev = vals[1] if len(vals) >= 2 else None
    return latest, prev


def fetch_events(
    key: str, start: date, end: date, session: requests.Session | None = None
) -> list[FredEvent]:
    """[start, end] 구간의 미국 매크로 발표 이벤트 목록. key 없으면 빈 리스트."""
    if not key:
        return []
    events: list[FredEvent] = []
    for release_id, title, series_id, importance, _unit in RELEASES:
        data = _get(
            "release/dates", key, release_id=release_id,
            include_release_dates_with_no_data="true",
            sort_order="asc", limit=1000,
        )
        if not data:
            continue
        latest, prev = _latest_two_observations(key, series_id)
        for row in data.get("release_dates", []):
            raw = row.get("date")
            if not raw:
                continue
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                continue
            if not (start <= d <= end):
                continue
            # 과거 이벤트만 실적치 부여(미래 예정일엔 아직 값 없음).
            is_past = d <= date.today()
            events.append(
                FredEvent(
                    event_date=d, title=title, release_id=release_id, series_id=series_id,
                    importance=importance,
                    latest_value=latest if is_past else None,
                    prev_value=prev if is_past else None,
                )
            )
    return events
