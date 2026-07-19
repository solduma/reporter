"""요인모형 베타 — 지수·개별주 일봉 수익률로 시장베타를 회귀 계산(순수 도메인).

Fama-French·APT 의 시장베타를 LLM 추정 대신 실데이터로 구한다. β = Cov(주식, 시장) / Var(시장)
(단순회귀 기울기). 일봉 로그수익률을 날짜로 정렬·정합해 계산한다. IO 없음 — 봉 시계열을 입력받는다.

SMB(규모)·HML(가치) 베타는 국내 개별 팩터 수익률 데이터가 없어 프록시로 근사한다(문서화):
- SMB: 시가총액이 작을수록 규모 프리미엄 노출↑ (로그시총 기반 −1~+1 정규화).
- HML: PBR 이 낮을수록(가치주) 가치 프리미엄 노출↑ (PBR 기반 −1~+1).
프리미엄(연 %)·무위험수익률은 config 상수(한국 시장 장기 관례)를 쓴다.
"""

from __future__ import annotations

import math

# 요인 프리미엄(연, 소수) — 한국 시장 장기 실증 관례. 개별 팩터 수익률 시계열이 없어 상수로 둔다.
# 무위험수익률은 국고채 3년 근사. 시장프리미엄(ERP)은 한국 6~7%, SMB/HML 은 미국 대비 보수적.
RISK_FREE = 0.032
MARKET_PREMIUM = 0.06
SMB_PREMIUM = 0.025
HML_PREMIUM = 0.035
# APT 거시요인 프리미엄(경기·금리·환율). 시장베타를 대리(macro_beta≈market_beta)해 근사한다.
APT_FACTOR_PREMIUMS = {"경기(시장)": 0.055, "금리민감": 0.02, "환율민감": 0.02}

# WACC 파라미터.
COST_OF_DEBT_SPREAD = 0.02  # 세전 부채비용 = rf + 신용스프레드
TAX_RATE = 0.22  # 법인세 실효세율(한국 근사) — 부채 이자 세금방패
# 영구성장률 상한은 rf(무위험수익률 ≈ 명목GDP성장, Damodaran) 로 유계 — DCF·요인모형 공통. 별도 GDP
# 상수캡 폐기(rf 와 일관). 단기 성장률은 forward 엔진 외삽 클립(±)에 위임 — 임의 상수캡 폐기.

# 요인모형 3단계(성장 유지→감쇠→영구) 파라미터. 저베타 이상현상(Frazzini-Pedersen 2014) 상
# raw 요인 Re 를 8.2% 총하한으로 clamp 하면 FF·APT 가 둘 다 눌려 동일해지고 성장주가 저평가된다
# (딥리서치 2건 결론). 그래서 (1) 총 Re 하한을 폐기하고 성장국면엔 rf+2% 완만한 하한만,
# (2) 폭발 방지는 '터미널'에만 최소 스프레드로, (3) 성장 유지구간(CAP)을 명시적으로 둔다.
GROWTH_FLOOR_PREMIUM = 0.02  # 성장국면 할인율 하한 = rf + 이 값. 극단 저베타 목표PER 폭주만 완화.
MIN_TERM_SPREAD = 0.045  # 터미널 (할인율 − g_L) 최소 스프레드 — target PER=1/(r−g) 폭발 방지.

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

    tax_rate·cost_of_debt 는 종목 실측값(결측 시 상수 TAX_RATE·rf+COST_OF_DEBT_SPREAD 폴백).
    equity_value·net_debt 는 같은 단위(억원). equity_value≤0 이면 Re 를 그대로 반환(부채가중 불가)."""
    e = equity_value
    d = max(0.0, net_debt or 0.0)  # 순현금(음수)은 부채 0 취급
    if e <= 0 or (e + d) <= 0:
        return cost_of_equity, [f"자기자본비용 {cost_of_equity:.1%} (자본구조 미반영)"]
    v = e + d
    t = tax_rate if tax_rate is not None else TAX_RATE
    rd = cost_of_debt if cost_of_debt is not None else (risk_free + COST_OF_DEBT_SPREAD)
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


def smb_beta(market_cap_eok: float | None) -> float:
    """규모(SMB) 노출 프록시. 시총이 작을수록 +1(소형 프리미엄 노출), 클수록 −0.3.

    로그시총 기준점: 3000억(중소형 경계)에서 0. 500억↓ ≈ +1, 20조↑ ≈ −0.3(대형주는 SMB 음).
    """
    if not market_cap_eok or market_cap_eok <= 0:
        return 0.0
    # log10(시총억): 소형(2.7=500억)~대형(5.3=20조). 3.48(3000억) 기준 반전 정규화.
    lg = math.log10(market_cap_eok)
    raw = (3.48 - lg) / 0.8  # 3000억에서 0, 한 자릿수당 ~1.25
    return round(max(-0.3, min(1.0, raw)), 3)


def hml_beta(pbr: float | None) -> float:
    """가치(HML) 노출 프록시. PBR 이 낮을수록 +1(가치주), 높을수록 −0.5(성장주).

    PBR 1.0 에서 0, 0.5↓ ≈ +1(깊은 가치), 3.0↑ ≈ −0.5(고성장). 결측·비정상이면 0.
    """
    if pbr is None or pbr <= 0:
        return 0.0
    raw = (1.0 - pbr) / 0.6  # PBR 1 에서 0, 0.4 에서 +1, 1.6 에서 -1
    return round(max(-0.5, min(1.0, raw)), 3)
