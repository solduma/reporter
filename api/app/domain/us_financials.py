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


_FY_MIN_DAYS = 350
_FY_MAX_DAYS = 380
_YEAR_TOL_DAYS = 20  # 전년 동기 매칭 허용 오차


def _periods(facts: dict, key: str, unit: str = "USD") -> dict[tuple[date, date], float]:
    """계정의 모든 duration 엔트리 {(start,end): val}. (start,end) 중복은 뒤값(정정) 우선."""
    acct = _gaap(facts).get(key)
    if not acct or unit not in acct.get("units", {}):
        return {}
    out: dict[tuple[date, date], float] = {}
    for row in acct["units"][unit]:
        s, e, v = row.get("start"), row.get("end"), row.get("val")
        if not s or not e or v is None:
            continue
        out[(date.fromisoformat(s), date.fromisoformat(e))] = float(v)
    return out


def _span(p: tuple[date, date]) -> int:
    return (p[1] - p[0]).days


def _ttm(facts: dict, key: str, unit: str = "USD") -> float | None:
    """정확한 TTM(최근 12개월) 합.

    US-GAAP 10-Q 는 분기 개별값을 주지만 **10-K 는 discrete Q4 를 안 주고 연간(FY)만** 준다.
    따라서 최근 4개 ~90일 분기를 무검증 합산하면 Q4 부재로 12개월 아닌 구간을 더해 값이 틀린다.
    올바른 TTM = 최근 FY + (FY 종료 후 분기 합) - (전년 동기 분기 합). FY 가 없으면 연속 4분기
    (합산 구간이 ~365일인지 검증)로 폴백한다.
    """
    periods = _periods(facts, key, unit)
    if not periods:
        return None
    quarters = {p: v for p, v in periods.items() if _Q_MIN_DAYS <= _span(p) <= _Q_MAX_DAYS}
    fys = sorted(
        (p for p in periods if _FY_MIN_DAYS <= _span(p) <= _FY_MAX_DAYS), key=lambda p: p[1]
    )
    if fys:
        fy = fys[-1]
        fy_val = periods[fy]
        # FY 종료 후 최신 분기들(오름차순).
        after = sorted((p for p in quarters if p[1] > fy[1]), key=lambda p: p[1])
        total = fy_val
        for qp in after:
            # 전년 동기 분기(end 가 약 1년 전) 매칭.
            prior_end = qp[1].replace(year=qp[1].year - 1)
            prior = next(
                (v for p, v in quarters.items() if abs((p[1] - prior_end).days) <= _YEAR_TOL_DAYS),
                None,
            )
            if prior is None:
                return None  # 전년 동기 없어 TTM 이동 불가 → 값 왜곡 방지 위해 미산출
            total += quarters[qp] - prior
        return total
    # 폴백: FY 엔트리 없음 → 연속 4분기, 단 합산 구간이 ~1년인지 확인.
    qs = sorted(quarters.items(), key=lambda kv: kv[0][1])
    if len(qs) < 4:
        return None
    last4 = qs[-4:]
    total_span = (last4[-1][0][1] - last4[0][0][0]).days
    if not (_FY_MIN_DAYS <= total_span <= _FY_MAX_DAYS):
        return None  # 분기 누락으로 4개가 1년을 안 덮음 → 미산출
    return sum(v for _p, v in last4)


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
