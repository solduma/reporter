"""시장베타 — 지수·개별주 일봉 수익률로 시장베타를 회귀 계산(순수 도메인).

CAPM 자기자본비용(COE)의 시장베타를 LLM 추정 대신 실데이터로 구한다. β = Cov(주식, 시장) / Var(시장)
(단순회귀 기울기). 일봉 로그수익률을 날짜로 정렬·정합해 계산한다. IO 없음 — 봉 시계열을 입력받는다.
무위험수익률·시장프리미엄은 config 상수(한국 시장 장기 관례)를 쓴다.
"""

from __future__ import annotations

import math

# 무위험수익률·ERP·실효세율·부채비용은 상수를 두지 않는다 — 전부 실측(ECOS·Damodaran·DART).
# 실측 결측 시 상수로 메우지 않고 해당 방식을 스킵(상수 근절). 영구성장률 상한도 rf(실측) 로 유계.

MIN_TERM_SPREAD = 0.045  # 터미널 (할인율 − g_L) 최소 스프레드 — target PER=1/(r−g) 폭발 방지(DCF).

# 경쟁우위기간 CAP(년) = 해자별 기준 × ROE 초과수익 지속성 조정. 리서치 B(Mauboussin CAP,
# fade rate 0.10~0.30 → CAP 3~10년) 기반. 1/WACC 방식은 저할인율에서 지평이 폭증(성장주 PER
# 폭발)해 폐기했다. 상한 12년(market-implied CAP 5~20년 중 보수적).
MOAT_CAP_YEARS = {"강": 10.0, "중": 6.0, "약": 3.0}
MAX_CAP_YEARS = 12.0


def competitive_advantage_period(
    roe: float | None, discount: float | None, moat: str | None
) -> tuple[float, list[str]]:
    """고성장·초과수익 지속기간 CAP(년). H-Model 이 생략한 '고성장 유지구간'의 정량 근거.

    원리: 해자별 기준연수(MOAT_CAP_YEARS)를 ROE 초과수익 지속성으로 [0.5,1.5]배 조정한다.
    지속성 = 스프레드(ROE−할인율)의 포화함수 spread/(spread+할인율) ∈ [0,1). 상한 MAX_CAP_YEARS.
    fade_years(1/WACC 방식)와 달리 저할인율에서 지평이 폭증하지 않는다. ROE·할인율 결측 시 지속성
    중립(0.5배)으로 보수 처리. 반환 CAP 는 유지기+감쇠기 합(각 CAP/2)."""
    base = MOAT_CAP_YEARS.get(moat or "", MOAT_CAP_YEARS["중"])
    if roe is None or discount is None or discount <= 0:
        cap = min(MAX_CAP_YEARS, base * 0.5)
        return round(cap, 1), [f"CAP {cap:.1f}년(ROE·할인율 미상 → 해자'{moat or '중'}' 기준 {base:g}년의 0.5배)"]
    roe_frac = roe / 100 if abs(roe) > 1 else roe
    spread = max(0.0, roe_frac - discount)
    persistence = spread / (spread + discount)  # [0,1)
    cap = min(MAX_CAP_YEARS, base * (0.5 + persistence))
    steps = [
        f"CAP = 해자'{moat or '중'}' 기준 {base:g}년 × (0.5 + 초과수익지속성 {persistence:.0%}) = {cap:.1f}년",
        f"(ROE {roe_frac:.1%} − 할인율 {discount:.1%} = 스프레드 {spread:+.1%}, 상한 {MAX_CAP_YEARS:g}년)",
    ]
    return round(cap, 1), steps


def wacc(
    cost_of_equity: float,
    equity_value: float,
    net_debt: float | None,
    risk_free: float,
    tax_rate: float | None = None,
    cost_of_debt: float | None = None,
) -> tuple[float, list[str]]:
    """WACC = Re·(E/V) + Rd(1−t)·(D/V). D 는 순차입(음수=순현금이면 0). 과정 스텝도 반환.

    tax_rate·cost_of_debt 는 종목 실측값(상수 폴백 없음). 부채 가중이 필요한데(d>0) 실측 세율·부채비용이
    결측이면 자본구조를 반영하지 않고 Re 를 그대로 반환(상수로 메우지 않음 — 상수 근절).
    equity_value·net_debt 는 같은 단위(억원). equity_value≤0 이면 Re 를 그대로 반환(부채가중 불가)."""
    e = equity_value
    d = max(0.0, net_debt or 0.0)  # 순현금(음수)은 부채 0 취급
    if e <= 0 or (e + d) <= 0:
        return cost_of_equity, [f"자기자본비용 {cost_of_equity:.1%} (자본구조 미반영)"]
    if d > 0 and (tax_rate is None or cost_of_debt is None):
        # 부채가 있는데 실측 세율·부채비용 결측 → 상수로 메우지 않고 Re(무부채 근사) 반환.
        return cost_of_equity, [f"자기자본비용 {cost_of_equity:.1%} (부채비용·세율 실측 결측 → 자본구조 미반영)"]
    v = e + d
    t = tax_rate or 0.0
    rd = cost_of_debt or 0.0
    rd_after_tax = rd * (1 - t)
    w = cost_of_equity * (e / v) + rd_after_tax * (d / v)
    steps = [
        f"자기자본비용 Re {cost_of_equity:.1%} × 자본비중 {e / v:.0%}",
        f"+ 세후부채비용 {rd_after_tax:.1%} × 부채비중 {d / v:.0%}",
        f"= WACC {w:.1%}",
    ]
    return w, steps


def _log_returns(closes: list[float]) -> list[float]:
    """종가 시계열 → 로그수익률. 0·음수 종가는 구간 건너뜀(정합용 인덱스는 호출측이 관리)."""
    out = []
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1], closes[i]
        out.append(math.log(p1 / p0) if p0 > 0 and p1 > 0 else 0.0)
    return out


def market_beta(
    stock: list[tuple[str, float]], index: list[tuple[str, float]], min_points: int = 60
) -> float | None:
    """시장베타 = Cov(주식수익률, 지수수익률) / Var(지수수익률). 공통 거래일로 정합해 회귀.

    stock·index 는 (날짜 iso, 종가) 리스트. 공통 날짜가 min_points 미만이거나 지수 분산 0이면 None.
    """
    idx_by_date = {d: c for d, c in index if c > 0}
    paired = [(c, idx_by_date[d]) for d, c in stock if c > 0 and d in idx_by_date]
    if len(paired) < min_points + 1:
        return None
    s_close = [p[0] for p in paired]
    i_close = [p[1] for p in paired]
    s_ret = _log_returns(s_close)
    i_ret = _log_returns(i_close)
    n = len(i_ret)
    if n < min_points:
        return None
    mean_i = sum(i_ret) / n
    mean_s = sum(s_ret) / n
    var_i = sum((x - mean_i) ** 2 for x in i_ret) / n
    if var_i <= 0:
        return None
    cov = sum((s_ret[k] - mean_s) * (i_ret[k] - mean_i) for k in range(n)) / n
    beta = cov / var_i
    # 극단값 방어: 실증 베타는 대략 -1~3 범위. 벗어나면 회귀 불안정(저유동성 등)으로 보고 클램프.
    return max(-1.0, min(3.0, round(beta, 3)))
