"""관세청 품목별 국가별 수출입실적 (data.go.kr 15100475).

엔드포인트: http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
- 필수: serviceKey, strtYymm, endYymm (조회기간 1년 이내). cntyCd 생략 시 전체 국가.
- 응답 XML: item{year, expDlr, impDlr, balPayments, hsCd, statKor, statCdCntnKor1}.
  year='총계' 는 기간 합계행. 월별 추이는 국가별 행을 월(year)로 합산한다.
serviceKey 는 Decoding 키를 requests params 로 넘겨 인코딩한다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

_URL = "http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"


@dataclass
class TradeMonth:
    period: str  # 'YYYY.MM'
    export_usd: int
    import_usd: int
    balance_usd: int


def _int(text: str | None) -> int:
    try:
        return int(text) if text else 0
    except ValueError:
        return 0


def fetch_trade_by_hs(
    api_key: str, hs_code: str, start_yymm: str, end_yymm: str, session: requests.Session
) -> list[TradeMonth]:
    """HS 품목의 월별 수출입 실적(전체 국가 합산)을 반환한다. 최신월 오름차순."""
    params = {
        "serviceKey": api_key,
        "strtYymm": start_yymm,
        "endYymm": end_yymm,
        "hsSgn": hs_code,
    }
    try:
        resp = session.get(_URL, params=params, timeout=20)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except (requests.RequestException, ElementTree.ParseError) as e:
        logger.warning("customs fetch failed hs=%s: %s", hs_code, e)
        return []

    if root.findtext(".//resultCode") not in ("00", None):
        logger.warning("customs result %s: %s", root.findtext(".//resultCode"), root.findtext(".//resultMsg"))
        return []

    # 국가별 행을 월(year)로 합산. '총계' 행은 제외(기간 전체 합계라 시계열 아님).
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in root.findall(".//item"):
        period = item.findtext("year")
        if not period or period == "총계":
            continue
        by_month[period][0] += _int(item.findtext("expDlr"))
        by_month[period][1] += _int(item.findtext("impDlr"))

    return [
        TradeMonth(period=p, export_usd=exp, import_usd=imp, balance_usd=exp - imp)
        for p, (exp, imp) in sorted(by_month.items())
    ]
