"""다중 밸류에이션 — 순수 계산 도메인(IO·프레임워크·LLM 모름).

딥다이브 5단계(Valuation)가 쓴다. 판단(예상 실적·목표 멀티플·성장률·할인율·베타 등 *가정*)은
LLM 이 근거와 함께 제시하고, **산식과 목표가 계산·과정 서술은 여기(재현 가능한 순수 함수)가 소유**한다.
환각 방지: 숫자가 스스로 굴러가지 않게, 모든 결과는 입력 가정에서 결정론적으로 유도된다.

8개 방식: DCF·DDM·자산가치·PER·PBR·EV/EBITDA·Fama-French·APT.
- PER/PBR/EV/EBITDA: 실데이터(eps·bps·ebitda) × LLM 목표 멀티플 → 주당 가치.
- DCF: 2단계(명시적 성장 n년 → 영구성장) FCFF 현가 + 잔존가치, 순부채 차감, 주식수로 나눔.
- DDM: 고든 성장(안정 배당) 또는 2단계 배당 현가.
- 자산가치: 지배주주 자본(장부) × LLM 프리미엄/할인(청산·재평가 반영).
- Fama-French / APT: 요인 노출×프리미엄 → 요구수익률(할인율). 목표 PER=1/(r-g) 로 EPS 에 적용.

각 방식은 ValuationResult(목표가·업사이드·신뢰도·가정·과정 스텝)를 낸다. 최종 목표가는
신뢰도 가중 평균(blend). 계산 불가(입력 결측)면 결과에서 제외하고 사유를 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 방식 표시명(프론트 라벨·서술 공용). 키는 기계 식별자.
METHOD_LABELS: dict[str, str] = {
    "per": "PER (주가수익비율)",
    "pbr": "PBR (주가순자산비율)",
    "ev_ebitda": "EV/EBITDA",
    "dcf": "DCF (현금흐름할인)",
    "ddm": "DDM (배당할인)",
    "asset": "자산가치 (Asset-Based)",
    "fama_french": "Fama-French 3요인",
    "apt": "APT (차익거래가격결정)",
}


@dataclass
class ValuationResult:
    """한 밸류에이션 방식의 결과. target_price 는 주당 원. process 는 사람이 읽는 계산 과정 스텝."""

    method: str  # 기계 식별자(METHOD_LABELS 키)
    label: str
    applicable: bool  # 계산에 필요한 입력이 충분했는가
    target_price: float | None = None
    upside_pct: float | None = None  # (target-current)/current × 100
    confidence: str = "중"  # 상|중|하 — 최종 blend 가중치·표시용
    assumptions: dict = field(default_factory=dict)  # 사용한 입력 가정(근거 표시)
    process: list[str] = field(default_factory=list)  # 계산 과정(스텝별 서술)
    note: str = ""  # 계산 불가 사유 또는 보충 설명


_CONF_WEIGHT = {"상": 3.0, "중": 2.0, "하": 1.0}


def _round_won(v: float) -> float:
    """주가는 원 단위(소수점 무의미). 큰 수는 정수, 저가주는 소수 보존 안 함."""
    return round(v)


def _upside(target: float | None, current: float | None) -> float | None:
    if target is None or current is None or current <= 0:
        return None
    return round((target - current) / current * 100, 1)


def _fmt(n: float | None) -> str:
    """서술용 숫자 포맷(원 단위 콤마). None 은 '?'."""
    if n is None:
        return "?"
    return f"{n:,.0f}" if abs(n) >= 100 else f"{n:,.2f}"


# ── 상대가치(멀티플) ────────────────────────────────────────────────────
def per_valuation(
    *, forward_eps: float | None, target_per: float | None, current_price: float | None
) -> ValuationResult:
    """목표가 = 예상 EPS × 목표 PER. 성장주·이익 창출 기업의 기본."""
    r = ValuationResult("per", METHOD_LABELS["per"], applicable=False)
    if forward_eps is None or target_per is None:
        r.note = "예상 EPS 또는 목표 PER 결측"
        return r
    if forward_eps <= 0:
        r.note = f"예상 EPS({_fmt(forward_eps)})가 0 이하 — PER 적용 불가(적자 기업)"
        return r
    target = _round_won(forward_eps * target_per)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.assumptions = {"forward_eps": forward_eps, "target_per": target_per}
    r.process = [
        f"예상 주당순이익(EPS) {_fmt(forward_eps)}원",
        f"목표 PER {target_per:g}배 적용",
        f"목표가 = {_fmt(forward_eps)} × {target_per:g} = {_fmt(target)}원",
    ]
    return r


def pbr_valuation(
    *, bps: float | None, target_pbr: float | None, current_price: float | None
) -> ValuationResult:
    """목표가 = 주당순자산(BPS) × 목표 PBR. 자산주·금융주·역발상에 유효."""
    r = ValuationResult("pbr", METHOD_LABELS["pbr"], applicable=False)
    if bps is None or target_pbr is None:
        r.note = "BPS 또는 목표 PBR 결측"
        return r
    if bps <= 0:
        r.note = f"BPS({_fmt(bps)})가 0 이하 — 자본잠식"
        return r
    target = _round_won(bps * target_pbr)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.assumptions = {"bps": bps, "target_pbr": target_pbr}
    r.process = [
        f"주당순자산(BPS) {_fmt(bps)}원",
        f"목표 PBR {target_pbr:g}배 적용",
        f"목표가 = {_fmt(bps)} × {target_pbr:g} = {_fmt(target)}원",
    ]
    return r


def ev_ebitda_valuation(
    *,
    forward_ebitda: float | None,  # 억원
    target_ev_ebitda: float | None,
    net_debt: float | None,  # 억원 (양수=순차입, 음수=순현금)
    shares: float | None,  # 주식수
    current_price: float | None,
) -> ValuationResult:
    """EV = EBITDA × 목표배수 → 시총 = EV − 순차입 → 목표가 = 시총/주식수. 자본구조 중립 비교."""
    r = ValuationResult("ev_ebitda", METHOD_LABELS["ev_ebitda"], applicable=False)
    if forward_ebitda is None or target_ev_ebitda is None or shares is None or shares <= 0:
        r.note = "예상 EBITDA·목표 EV/EBITDA·주식수 중 결측"
        return r
    if forward_ebitda <= 0:
        r.note = f"예상 EBITDA({_fmt(forward_ebitda)}억)가 0 이하"
        return r
    nd = net_debt or 0.0
    ev = forward_ebitda * target_ev_ebitda  # 억원
    equity_value = ev - nd  # 억원
    if equity_value <= 0:
        r.note = f"EV({_fmt(ev)}억) − 순차입({_fmt(nd)}억) ≤ 0 — 지분가치 없음"
        return r
    target = _round_won(equity_value * 1e8 / shares)  # 억원→원 후 주식수로
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.assumptions = {
        "forward_ebitda_eok": forward_ebitda, "target_ev_ebitda": target_ev_ebitda,
        "net_debt_eok": nd, "shares": shares,
    }
    r.process = [
        f"예상 EBITDA {_fmt(forward_ebitda)}억원 × 목표 배수 {target_ev_ebitda:g} = EV {_fmt(ev)}억원",
        f"지분가치 = EV {_fmt(ev)}억 − 순차입 {_fmt(nd)}억 = {_fmt(equity_value)}억원",
        f"목표가 = {_fmt(equity_value)}억 ÷ {_fmt(shares)}주 = {_fmt(target)}원",
    ]
    return r


# ── 절대가치 ────────────────────────────────────────────────────────────
def dcf_valuation(
    *,
    fcf_base: float | None,  # 기준연도 잉여현금흐름(억원)
    growth_rate: float | None,  # 명시적 구간 연평균 성장률(예 0.10)
    years: int,  # 명시적 구간 연수
    terminal_growth: float | None,  # 영구성장률(예 0.02)
    discount_rate: float | None,  # WACC(예 0.09)
    net_debt: float | None,  # 억원
    shares: float | None,
    current_price: float | None,
) -> ValuationResult:
    """2단계 DCF: 명시적 n년 성장 FCF 현가 + 영구성장 잔존가치 → 기업가치 → 지분가치/주식수."""
    r = ValuationResult("dcf", METHOD_LABELS["dcf"], applicable=False)
    if None in (fcf_base, growth_rate, terminal_growth, discount_rate, shares) or shares <= 0:
        r.note = "FCF·성장률·영구성장률·할인율·주식수 중 결측"
        return r
    if discount_rate <= terminal_growth:
        r.note = f"할인율({discount_rate:.1%}) ≤ 영구성장률({terminal_growth:.1%}) — 잔존가치 발산"
        return r
    if fcf_base <= 0:
        r.note = f"기준 FCF({_fmt(fcf_base)}억)가 0 이하 — DCF 부적합"
        return r
    n = max(1, min(int(years), 15))  # 방어: 1~15년
    pv_explicit = 0.0
    fcf = fcf_base
    for t in range(1, n + 1):
        fcf = fcf * (1 + growth_rate)
        pv_explicit += fcf / (1 + discount_rate) ** t
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** n
    enterprise_value = pv_explicit + pv_terminal  # 억원
    nd = net_debt or 0.0
    equity_value = enterprise_value - nd
    if equity_value <= 0:
        r.note = f"기업가치({_fmt(enterprise_value)}억) − 순차입({_fmt(nd)}억) ≤ 0"
        return r
    target = _round_won(equity_value * 1e8 / shares)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.confidence = "하"  # 가정 민감도가 커 기본 신뢰도 낮게(LLM 이 상향 가능)
    r.assumptions = {
        "fcf_base_eok": fcf_base, "growth_rate": growth_rate, "years": n,
        "terminal_growth": terminal_growth, "discount_rate": discount_rate,
        "net_debt_eok": nd, "shares": shares,
    }
    r.process = [
        f"기준 FCF {_fmt(fcf_base)}억원, {n}년간 연 {growth_rate:.1%} 성장 가정",
        f"명시적 구간 현가 합 {_fmt(pv_explicit)}억원 (할인율 {discount_rate:.1%})",
        f"영구성장 {terminal_growth:.1%} → 잔존가치 {_fmt(terminal_value)}억, 현가 {_fmt(pv_terminal)}억원",
        f"기업가치 {_fmt(enterprise_value)}억 − 순차입 {_fmt(nd)}억 = 지분가치 {_fmt(equity_value)}억원",
        f"목표가 = {_fmt(equity_value)}억 ÷ {_fmt(shares)}주 = {_fmt(target)}원",
    ]
    return r


def ddm_valuation(
    *,
    dps: float | None,  # 주당배당금(원)
    dividend_growth: float | None,  # 배당성장률(예 0.03)
    cost_of_equity: float | None,  # 자기자본비용(예 0.08)
    current_price: float | None,
) -> ValuationResult:
    """고든 성장모형: 목표가 = D1 / (r − g). 안정 배당주(금융·유틸리티·배당성장주)에 유효."""
    r = ValuationResult("ddm", METHOD_LABELS["ddm"], applicable=False)
    if None in (dps, dividend_growth, cost_of_equity):
        r.note = "주당배당금·배당성장률·자기자본비용 중 결측"
        return r
    if dps <= 0:
        r.note = "무배당 또는 배당 결측 — DDM 부적합"
        return r
    if cost_of_equity <= dividend_growth:
        r.note = f"자본비용({cost_of_equity:.1%}) ≤ 배당성장률({dividend_growth:.1%}) — 발산"
        return r
    d1 = dps * (1 + dividend_growth)
    target = _round_won(d1 / (cost_of_equity - dividend_growth))
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.assumptions = {"dps": dps, "dividend_growth": dividend_growth, "cost_of_equity": cost_of_equity}
    r.process = [
        f"주당배당금(DPS) {_fmt(dps)}원, 배당성장률 {dividend_growth:.1%}",
        f"차기 배당 D1 = {_fmt(dps)} × (1+{dividend_growth:.1%}) = {_fmt(d1)}원",
        f"목표가 = D1 ÷ (자본비용 {cost_of_equity:.1%} − 성장 {dividend_growth:.1%}) = {_fmt(target)}원",
    ]
    return r


def asset_valuation(
    *,
    book_equity_per_share: float | None,  # 주당순자산(장부, 원) = BPS
    asset_premium: float | None,  # 재평가/청산 배수(예 0.8=청산할인, 1.2=재평가할증)
    current_price: float | None,
) -> ValuationResult:
    """자산가치: 주당순자산(장부) × 재평가/청산 배수. 자산주·지주사·청산가치 접근."""
    r = ValuationResult("asset", METHOD_LABELS["asset"], applicable=False)
    if book_equity_per_share is None or asset_premium is None:
        r.note = "주당순자산 또는 재평가 배수 결측"
        return r
    if book_equity_per_share <= 0:
        r.note = "자본잠식 — 자산가치 접근 부적합"
        return r
    target = _round_won(book_equity_per_share * asset_premium)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.assumptions = {"book_equity_per_share": book_equity_per_share, "asset_premium": asset_premium}
    kind = "청산할인" if asset_premium < 1 else ("재평가할증" if asset_premium > 1 else "장부가")
    r.process = [
        f"주당순자산(장부) {_fmt(book_equity_per_share)}원",
        f"{kind} 배수 {asset_premium:g} 적용(부동산·투자자산 재평가·청산가치 반영)",
        f"목표가 = {_fmt(book_equity_per_share)} × {asset_premium:g} = {_fmt(target)}원",
    ]
    return r


# ── 요인모형(요구수익률 → 목표 PER) ──────────────────────────────────────
@dataclass
class FactorExposure:
    """요인 하나: 베타(노출)·프리미엄(연 %, 소수). name 은 표시용."""

    name: str
    beta: float
    premium: float


def _required_return(risk_free: float, factors: list[FactorExposure]) -> tuple[float, list[str]]:
    """요구수익률 r = rf + Σ(βi × premiumi). 과정 스텝도 반환."""
    r = risk_free
    steps = [f"무위험수익률 {risk_free:.1%}"]
    for f in factors:
        contrib = f.beta * f.premium
        r += contrib
        steps.append(f"+ {f.name}: β {f.beta:g} × 프리미엄 {f.premium:.1%} = {contrib:+.2%}")
    steps.append(f"= 요구수익률 {r:.1%}")
    return r, steps


def _factor_model_valuation(
    method: str,
    *,
    forward_eps: float | None,
    risk_free: float | None,
    factors: list[FactorExposure],
    earnings_growth: float | None,  # 이익 성장률 g (목표 PER = 1/(r−g))
    current_price: float | None,
) -> ValuationResult:
    """요인모형(Fama-French/APT) 공용: 요구수익률 r 유도 → 목표 PER=1/(r−g) → 목표가=EPS×PER."""
    r = ValuationResult(method, METHOD_LABELS[method], applicable=False)
    if forward_eps is None or risk_free is None or earnings_growth is None or not factors:
        r.note = "예상 EPS·무위험수익률·이익성장률·요인노출 중 결측"
        return r
    if forward_eps <= 0:
        r.note = f"예상 EPS({_fmt(forward_eps)})가 0 이하 — 요인모형 PER 적용 불가"
        return r
    req_return, ret_steps = _required_return(risk_free, factors)
    if req_return <= earnings_growth:
        r.note = f"요구수익률({req_return:.1%}) ≤ 이익성장률({earnings_growth:.1%}) — 목표 PER 발산"
        return r
    target_per = 1.0 / (req_return - earnings_growth)
    target = _round_won(forward_eps * target_per)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.confidence = "하"  # 요인·프리미엄 추정 불확실성이 커 기본 낮게
    r.assumptions = {
        "forward_eps": forward_eps, "risk_free": risk_free, "earnings_growth": earnings_growth,
        "factors": [{"name": f.name, "beta": f.beta, "premium": f.premium} for f in factors],
        "required_return": round(req_return, 4), "implied_target_per": round(target_per, 2),
    }
    r.process = [
        *ret_steps,
        f"목표 PER = 1 ÷ (요구수익률 {req_return:.1%} − 이익성장 {earnings_growth:.1%}) = {target_per:.1f}배",
        f"목표가 = 예상 EPS {_fmt(forward_eps)} × {target_per:.1f} = {_fmt(target)}원",
    ]
    return r


def fama_french_valuation(**kwargs) -> ValuationResult:
    """Fama-French 3요인(시장·규모SMB·가치HML) 요구수익률 → 목표 PER → 목표가."""
    return _factor_model_valuation("fama_french", **kwargs)


def apt_valuation(**kwargs) -> ValuationResult:
    """APT: 임의 다요인(금리·경기·인플레·환율 등) 요구수익률 → 목표 PER → 목표가."""
    return _factor_model_valuation("apt", **kwargs)


# ── 최종 목표가 blend ────────────────────────────────────────────────────
@dataclass
class ValuationSummary:
    """다중 밸류에이션 종합. final_target 은 적용 가능 방식의 신뢰도 가중 평균."""

    final_target: float | None
    final_upside_pct: float | None
    current_price: float | None
    method_count: int  # 적용된(계산 성공) 방식 수
    results: list[ValuationResult]


def blend(results: list[ValuationResult], current_price: float | None) -> ValuationSummary:
    """적용 가능 방식의 목표가를 신뢰도(상3·중2·하1) 가중 평균해 최종 목표가를 낸다.

    이상치(중앙값 대비 ±60% 초과)는 blend 에서 제외해 한 방식의 폭주가 최종가를 왜곡하지 않게 한다.
    (결과 목록에는 남겨 사용자가 본다 — 제외 사유를 note 에 표기.)
    """
    applicable = [r for r in results if r.applicable and r.target_price and r.target_price > 0]
    if not applicable:
        return ValuationSummary(None, None, current_price, 0, results)

    targets = sorted(r.target_price for r in applicable)  # type: ignore[misc]
    mid = targets[len(targets) // 2]  # 중앙값
    kept: list[ValuationResult] = []
    for r in applicable:
        if mid > 0 and abs(r.target_price - mid) / mid > 0.6:  # type: ignore[operator]
            r.note = (r.note + " " if r.note else "") + "이상치로 최종 평균에서 제외"
        else:
            kept.append(r)
    pool = kept or applicable  # 전부 이상치면(분산 큼) 그냥 다 씀

    wsum = sum(_CONF_WEIGHT.get(r.confidence, 2.0) for r in pool)
    final = sum(r.target_price * _CONF_WEIGHT.get(r.confidence, 2.0) for r in pool) / wsum  # type: ignore[operator]
    final_t = _round_won(final)
    return ValuationSummary(
        final_target=final_t,
        final_upside_pct=_upside(final_t, current_price),
        current_price=current_price,
        method_count=len(applicable),
        results=results,
    )
