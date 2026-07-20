"""한국은행 ECOS — 시장금리(무위험수익률) + 기준금리 시계열.

무료 API 키(ecos.bok.or.kr/api). 경로 파라미터 REST(쿼리스트링 아님):
  /api/StatisticSearch/{KEY}/json/kr/{START}/{END}/{STAT}/{CYCLE}/{FROM}/{TO}/{ITEM}
시장금리 일별 STAT=817Y002, CYCLE=D, 날짜 YYYYMMDD, 단위 연%. 응답 StatisticSearch.row[].

DCF 무위험수익률용 국고채 3년/10년 + 캘린더 사후 업데이트용 기준금리(722Y001).
key 미설정/실패 시 빈 리스트(graceful degrade).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

logger = logging.getLogger(__name__)

_BASE = "https://ecos.bok.or.kr/api"
_STAT_MARKET_RATE = "817Y002"  # 시장금리(일별)
_STAT_BASE_RATE = "722Y001"  # 기준금리(월별, 통화정책)

# maturity 라벨 → ECOS ITEM_CODE(국고채). DCF 무위험수익률 후보.
TREASURY_ITEMS: dict[str, str] = {
    "kr_treasury_3y": "010200000",  # 국고채 3년(무위험 기준)
    "kr_treasury_10y": "010210000",  # 국고채 10년(장기 명목성장 근사)
}

# 기준금리 ITEM_CODE
_BASE_RATE_ITEM = "0101000"  # 기준금리


@dataclass
class RateObservation:
    rate_date: date
    rate: float  # 연 % (예 3.24)
    maturity: str  # TREASURY_ITEMS 키


def _get(path: str) -> dict | None:
    try:
        resp = requests.get(f"{_BASE}/{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("ECOS %s failed: %s", path.split("/")[0], e)
        return None


def _parse_date(time_str: str) -> date | None:
    """ECOS TIME 'YYYYMMDD' → date. 파싱 실패 시 None."""
    if len(time_str) != 8 or not time_str.isdigit():
        return None
    return date(int(time_str[:4]), int(time_str[4:6]), int(time_str[6:8]))


def fetch_market_rate(
    key: str, item_code: str, maturity: str, start: date, end: date, *, limit: int = 100
) -> list[RateObservation]:
    """국고채 등 시장금리 일별 관측치. key 없거나 실패 시 빈 리스트."""
    if not key:
        return []
    path = (
        f"StatisticSearch/{key}/json/kr/1/{limit}/{_STAT_MARKET_RATE}/D/"
        f"{start:%Y%m%d}/{end:%Y%m%d}/{item_code}"
    )
    data = _get(path)
    if not data:
        return []
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    out: list[RateObservation] = []
    for r in rows:
        d = _parse_date(str(r.get("TIME") or ""))
        val = r.get("DATA_VALUE")
        if d is None or val is None:
            continue
        try:
            out.append(RateObservation(rate_date=d, rate=float(val), maturity=maturity))
        except (TypeError, ValueError):
            continue
    return out


def fetch_base_rate(
    key: str, start: date, end: date, *, limit: int = 20
) -> list[RateObservation]:
    """한국은행 기준금리 월별 관측치. key 없거나 실패 시 빈 리스트.

    기준금리는 월별 통계(STAT=722Y001, ITEM=0101000, CYCLE=M). 변경이 있을 때만 row 가 생기므로
    limit 은 넉넉히(20이면 20개월). 반환값 maturity="base_rate" 고정.
    """
    if not key:
        return []
    path = (
        f"StatisticSearch/{key}/json/kr/1/{limit}/{_STAT_BASE_RATE}/M/"
        f"{start:%Y%m%d}/{end:%Y%m%d}/{_BASE_RATE_ITEM}"
    )
    data = _get(path)
    if not data:
        return []
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    out: list[RateObservation] = []
    for r in rows:
        d = _parse_date(str(r.get("TIME") or ""))
        val = r.get("DATA_VALUE")
        if d is None or val is None:
            continue
        try:
            out.append(RateObservation(rate_date=d, rate=float(val), maturity="base_rate"))
        except (TypeError, ValueError):
            continue
    return out
