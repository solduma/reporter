"""Damodaran 국가별 자기자본위험프리미엄(ERP) — 밸류에이션 시장프리미엄 실측.

무료 공개 xlsx(pages.stern.nyu.edu/~adamodar). 'ERPs by country' 시트에서 국가별 Total Equity
Risk Premium 을 파싱한다. 밸류에이션의 MARKET_PREMIUM 상수(6% 관례)를 실측 ERP 로 대체하기 위함.
갱신은 월 단위(Damodaran 이 분기·수시 갱신). 실패/파싱불가 시 None(상수 폴백에 위임, graceful degrade).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xlsx"
_SHEET = "ERPs by country"
_COUNTRY_COL = 0  # 국가명
_ERP_COL = 4  # Total Equity Risk Premium(rating-based). row7 헤더로 확인.


@dataclass
class CountryErp:
    country: str
    erp: float  # 소수(0.0487 = 4.87%)


def fetch_country_erp(country: str = "Korea") -> CountryErp | None:
    """Damodaran xlsx 에서 지정 국가 Total ERP. 네트워크·파싱 실패 시 None."""
    try:
        resp = requests.get(_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
        import openpyxl  # 지연 import(무거운 의존성, 이 경로에서만 필요)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        logger.warning("Damodaran ERP fetch/parse failed: %s", e)
        return None
    try:
        ws = wb[_SHEET]
        for row in ws.iter_rows(values_only=True):
            name = row[_COUNTRY_COL] if row else None
            # 정확 일치(예: 'Korea' vs 'Korea, D.P.R.' 오매칭 방지).
            if isinstance(name, str) and name.strip() == country:
                erp = row[_ERP_COL]
                if isinstance(erp, int | float) and 0 < erp < 1:
                    return CountryErp(country=country, erp=float(erp))
                return None
    finally:
        wb.close()
    return None
