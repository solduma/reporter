"""US 재무 지표 계산 — SEC EDGAR companyfacts(XBRL) → TTM·PER/PBR/ROE/PSR.

순수 도메인 로직(I/O 없음). 입력은 companyfacts dict + 시가총액이고, 영속화·HTTP 를 모른다.

US-GAAP 특성(KR DART 와 다름):
- 10-Q 는 분기 개별값을 보고한다(DART 의 회계연도 누적 YTD 아님). 따라서 TTM = 최근 4개 분기 합.
- companyfacts 는 같은 기간을 여러 정정 공시로 중복 수록하고, 분기값과 연간/YTD 값이 units 에
  섞여 있다. → span(기간 일수)으로 분기(~90일)만 골라내고, (start,end) 중복은 마지막 값으로 접는다.
- 매출 계정은 회사마다 'Revenues' 또는 'RevenueFromContractWithCustomerExcludingAssessedTax'.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# 분기 span 허용 범위(일). 10-Q 분기는 약 91일 — 정확히 3개월이 아니라 회계주(週) 기준이라 폭을 둔다.
_Q_MIN_DAYS = 80
_Q_MAX_DAYS = 100

_REVENUE_KEYS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")


@dataclass
class UsFinancials:
    ttm_revenue: float | None  # 최근 4분기 매출 합(USD)
    ttm_net_income: float | None
    ttm_operating_income: float | None
    ttm_eps: float | None  # 최근 4분기 희석 EPS 합
    equity: float | None  # 최신 지배자본(instant)
    shares: float | None  # 최신 상장주식수
    per: float | None  # 시총 / TTM 순이익
    pbr: float | None  # 시총 / 자본
    psr: float | None  # 시총 / TTM 매출
    roe: float | None  # TTM 순이익 / 자본 (%)


def _gaap(facts: dict) -> dict:
    return facts.get("facts", {}).get("us-gaap", {})


def _discrete_quarters(facts: dict, key: str, unit: str = "USD") -> list[tuple[date, float]]:
    """계정의 분기 개별값 [(end_date, val)] 오름차순. span 으로 분기만 추리고 (start,end) 중복 접기.

    같은 (start,end) 가 여러 번 나오면(정정 공시) 뒤에 오는 값으로 덮는다(companyfacts 는
    대체로 시간순). 반환은 end 오름차순.
    """
    acct = _gaap(facts).get(key)
    if not acct or unit not in acct.get("units", {}):
        return []
    by_period: dict[tuple[str, str], float] = {}
    for row in acct["units"][unit]:
        start, end = row.get("start"), row.get("end")
        val = row.get("val")
        if not start or not end or val is None:
            continue
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if _Q_MIN_DAYS <= span <= _Q_MAX_DAYS:
            by_period[(start, end)] = float(val)
    return [(date.fromisoformat(end), v) for (_start, end), v in sorted(by_period.items(), key=lambda kv: kv[0][1])]


def _ttm(facts: dict, key: str, unit: str = "USD") -> float | None:
    """최근 4개 분기 개별값 합(TTM). 4개 미만이면 None."""
    quarters = _discrete_quarters(facts, key, unit)
    if len(quarters) < 4:
        return None
    return sum(v for _end, v in quarters[-4:])


def _ttm_revenue(facts: dict) -> float | None:
    """매출 TTM — 회사별 계정명 차이를 흡수(둘 중 분기 데이터가 있는 것)."""
    for key in _REVENUE_KEYS:
        v = _ttm(facts, key)
        if v is not None:
            return v
    return None


def _latest_instant(facts: dict, key: str, unit: str = "USD") -> float | None:
    """시점(instant) 계정의 최신값(자본·주식수 등). end 최신."""
    acct = _gaap(facts).get(key)
    if acct is None:
        # dei 계정(주식수)은 us-gaap 밖 → 호출측이 _latest_dei 사용
        return None
    if unit not in acct.get("units", {}):
        return None
    rows = [r for r in acct["units"][unit] if r.get("end") and r.get("val") is not None]
    if not rows:
        return None
    return float(max(rows, key=lambda r: r["end"])["val"])


def _latest_shares(facts: dict) -> float | None:
    """최신 상장주식수(dei.EntityCommonStockSharesOutstanding)."""
    dei = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not dei:
        return None
    for unit_rows in dei.get("units", {}).values():
        rows = [r for r in unit_rows if r.get("end") and r.get("val") is not None]
        if rows:
            return float(max(rows, key=lambda r: r["end"])["val"])
    return None


def compute(facts: dict, market_cap: float | None) -> UsFinancials:
    """companyfacts + 시가총액(USD) → US 밸류에이션 지표.

    market_cap 은 (분기말 종가 x 주식수)로 호출측이 근사해 넘긴다(EDGAR 엔 시총·주가 없음).
    지표는 시총·자본·TTM 이 있어야 산출되고, 없으면 해당 항목 None.
    """
    ttm_rev = _ttm_revenue(facts)
    ttm_ni = _ttm(facts, "NetIncomeLoss")
    ttm_op = _ttm(facts, "OperatingIncomeLoss")
    ttm_eps = _ttm(facts, "EarningsPerShareDiluted", unit="USD/shares")
    equity = _latest_instant(facts, "StockholdersEquity")
    shares = _latest_shares(facts)

    per = round(market_cap / ttm_ni, 2) if (market_cap and ttm_ni and ttm_ni > 0) else None
    pbr = round(market_cap / equity, 2) if (market_cap and equity and equity > 0) else None
    psr = round(market_cap / ttm_rev, 2) if (market_cap and ttm_rev and ttm_rev > 0) else None
    roe = round(ttm_ni / equity * 100, 1) if (ttm_ni is not None and equity and equity > 0) else None

    return UsFinancials(
        ttm_revenue=ttm_rev,
        ttm_net_income=ttm_ni,
        ttm_operating_income=ttm_op,
        ttm_eps=round(ttm_eps, 2) if ttm_eps is not None else None,
        equity=equity,
        shares=shares,
        per=per,
        pbr=pbr,
        psr=psr,
        roe=roe,
    )
