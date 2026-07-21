"""다중 밸류에이션 — 순수 계산 도메인(IO·프레임워크·LLM 모름).

딥다이브 5단계(Valuation)가 쓴다. 판단(예상 실적·목표 멀티플·성장률·할인율·베타 등 *가정*)은
LLM 이 근거와 함께 제시하고, **산식과 목표가 계산·과정 서술은 여기(재현 가능한 순수 함수)가 소유**한다.
환각 방지: 숫자가 스스로 굴러가지 않게, 모든 결과는 입력 가정에서 결정론적으로 유도된다.

5개 방식: PER·PBR·EV/EBITDA·DCF·DDM. 모두 결정론(배수·성장률·자본비용을
호출측이 실데이터로 확정해 넘기고, 여기선 산식만).
- PER/PBR/EV/EBITDA: 실데이터(eps·bps·ebitda) × 목표 멀티플(정당배수/밴드) → 주당 가치.
- DCF: 2/3단계(명시적 성장 → 영구성장) FCFF 현가 + 잔존가치, 순부채 차감, 주식수로 나눔.
- DDM: 고든 성장(안정 배당).

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
    "ggm_dcf": "GGM DCF (고든성장)",
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
def _band_warning(target: float, band: dict | None, unit: str) -> str:
    """리레이팅된 목표배수가 과거 밴드[p25,p75] 밖이면 위치 안내. clamp 하지 않고 사유만 노출(soft).

    band = {median, p25, p75, n}. 밴드 없거나 유효 표본 부족(n<4)이면 경고 없음.
    목표배수는 밴드 중앙값 × PEG 리레이팅(성장 가속/둔화)이라 밴드 밖이 정상 — 리레이팅 방향의 크기만 안내한다.
    """
    if not band or band.get("n", 0) < 4:
        return ""
    p25, p75, med = band.get("p25"), band.get("p75"), band.get("median")
    if p25 is None or p75 is None:
        return ""
    yrs = f"{band['n']}개 분기"
    if target > p75:
        return (f"목표 {unit} {target:g}배는 과거 밴드(중앙값 {med:g}, {p25:g}~{p75:g}배, {yrs}) 상회 "
                f"— forward 성장 가속에 따른 리레이팅")
    if target < p25:
        return (f"목표 {unit} {target:g}배는 과거 밴드(중앙값 {med:g}, {p25:g}~{p75:g}배, {yrs}) 하회 "
                f"— forward 성장 둔화에 따른 디레이팅")
    return ""


def per_valuation(
    *, forward_eps: float | None, target_per: float | None, current_price: float | None,
    per_band: dict | None = None, per_source: str = "",
) -> ValuationResult:
    """목표가 = 예상 EPS × 목표 PER. 성장주·이익 창출 기업의 기본.

    forward_eps·target_per 는 결정론적으로 산출돼 들어온다(외삽·HITL EPS, 밴드 PEG 리레이팅 PER). per_source
    는 목표배수 출처(예 '밴드 N배 × EPS성장 리레이팅 ×1.3') — process 에 노출해 재현성을 투명화한다.
    per_band 가 있으면 목표 PER 의 밴드 대비 위치(리레이팅 방향)를 안내한다(soft).
    """
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
    src = f" ({per_source})" if per_source else ""
    r.process = [
        f"예상 주당순이익(EPS) {_fmt(forward_eps)}원",
        f"목표 PER {target_per:g}배 적용{src}",
        f"목표가 = {_fmt(forward_eps)} × {target_per:g} = {_fmt(target)}원",
    ]
    r.note = _band_warning(target_per, per_band, "PER")
    return r


def pbr_valuation(
    *, bps: float | None, target_pbr: float | None, current_price: float | None,
    pbr_band: dict | None = None, pbr_source: str = "",
) -> ValuationResult:
    """목표가 = 주당순자산(BPS) × 목표 PBR. 자산주·금융주·역발상에 유효.

    bps·target_pbr 은 결정론적으로 산출돼 들어온다(BPS 앵커, 밴드 ROE 리레이팅 PBR).
    pbr_source 는 배수 출처(재현성), pbr_band 는 과거 밴드(리레이팅 방향 안내).
    """
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
    src = f" ({pbr_source})" if pbr_source else ""
    r.process = [
        f"주당순자산(BPS) {_fmt(bps)}원",
        f"목표 PBR {target_pbr:g}배 적용{src}",
        f"목표가 = {_fmt(bps)} × {target_pbr:g} = {_fmt(target)}원",
    ]
    r.note = _band_warning(target_pbr, pbr_band, "PBR")
    return r


def ev_ebitda_valuation(
    *,
    forward_ebitda: float | None,  # 억원
    target_ev_ebitda: float | None,
    net_debt: float | None,  # 억원 (양수=순차입, 음수=순현금)
    shares: float | None,  # 주식수
    current_price: float | None,
    ev_band: dict | None = None, ev_source: str = "",
) -> ValuationResult:
    """EV = EBITDA × 목표배수 → 시총 = EV − 순차입 → 목표가 = 시총/주식수. 자본구조 중립 비교.

    forward_ebitda·net_debt·shares 는 결정론 앵커, target_ev_ebitda 는 밴드 EBITDA성장 리레이팅(코드 확정).
    ev_source 는 배수 출처(재현성), ev_band 는 과거 밴드(리레이팅 방향 안내).
    """
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
    src = f" ({ev_source})" if ev_source else ""
    r.process = [
        f"예상 EBITDA {_fmt(forward_ebitda)}억원 × 목표 배수 {target_ev_ebitda:g}{src} = EV {_fmt(ev)}억원",
        f"지분가치 = EV {_fmt(ev)}억 − 순차입 {_fmt(nd)}억 = {_fmt(equity_value)}억원",
        f"목표가 = {_fmt(equity_value)}억 ÷ {_fmt(shares)}주 = {_fmt(target)}원",
    ]
    r.note = _band_warning(target_ev_ebitda, ev_band, "EV/EBITDA")
    return r


# ── 절대가치 ────────────────────────────────────────────────────────────
# 명시적 구간과 안정성장의 성장률 격차가 이 값 이하면 2단계(즉시 전환), 초과면 3단계(전환기).
# Damodaran: 안정률 ±8% 이내면 급격한 전환이 아니라 2단계로 충분, 초과(고성장주)면 전환기 필요.
_TWO_STAGE_GAP = 0.08


def dcf_valuation(
    *,
    fcf_base: float | None,  # 기준연도 잉여현금흐름(억원)
    growth_rate: float | None,  # 명시적(고성장) 구간 연평균 성장률(예 0.10)
    years: int,  # 명시적 구간 연수(roe·moat 로 CAP 산정 시 그 값으로 대체)
    terminal_growth: float | None,  # 영구성장률(예 0.02) — rf/경제성장으로 상한
    discount_rate: float | None,  # WACC(예 0.09)
    net_debt: float | None,  # 억원
    shares: float | None,
    current_price: float | None,
    roe: float | None = None,  # ROIC 대리 — CAP 산정 + 터미널 성장 상한(ROIC 초과분만 가치)
    moat: str | None = None,  # 해자 → CAP 기준연수
    risk_free: float | None = None,  # (미사용, 시그니처 호환용) 영구성장 상한 제거로 더는 안 씀
) -> ValuationResult:
    """FCFF DCF. 고성장주는 3단계(고성장 유지→선형 감쇠→영구), 완만성장주는 2단계로 자동 선택.

    Damodaran/CFA/McKinsey: 2단계의 '즉시 영구 전환'은 고성장주를 왜곡하므로, 성장률이 안정률보다
    8%p 초과면 전환기를 둔 3단계(CAP·선형 감쇠)를 쓴다. 영구성장률은 호출측이 실측
    금리(국고채 10년) 기반으로 확정해 넘기므로 상한을 두지 않는다(할인율≤영구성장 발산만 방어)."""
    from app.domain import beta as _beta

    r = ValuationResult("dcf", METHOD_LABELS["dcf"], applicable=False)
    if fcf_base is None:
        r.note = "FCFF(NOPAT+D&A−CAPEX) 결측 — D&A·CAPEX 미백필 시 DCF 부적합(순이익 근사 폴백 안 함)"
        return r
    if None in (growth_rate, terminal_growth, discount_rate, shares) or shares <= 0:
        r.note = "성장률·영구성장률·할인율·주식수 중 결측"
        return r
    # 영구성장률은 입력값 그대로 사용(상한 없음 — 실측 금리 기반이라 인위적 캡 불필요).
    # 단, 할인율 ≤ 영구성장률이면 고든 잔존가치가 발산/음수라 방어.
    g_l = terminal_growth
    if discount_rate <= g_l:
        r.note = f"할인율({discount_rate:.1%}) ≤ 영구성장률({g_l:.1%}) — 잔존가치 발산"
        return r
    # 저베타주는 CAPM WACC 가 낮아 (r−g_L) 스프레드가 좁아지면 고든 잔존가치 1/(r−g)가 폭발한다
    # (저베타 이상현상). 최소 터미널 스프레드로 유계(할인율만 상향, g 는 실측 유지).
    r_disc = max(discount_rate, g_l + _beta.MIN_TERM_SPREAD)
    if fcf_base <= 0:
        r.note = f"기준 FCF({_fmt(fcf_base)}억)가 0 이하 — DCF 부적합"
        return r

    # 성장 프로필로 2단계/3단계 선택. 고성장(격차>8%p)이면 CAP 기반 3단계.
    # g_s 는 forward 엔진에서 이미 ±클립돼 옴(임의 상수캡 제거) — 명시적 구간은 유한 합이라 발산 없음.
    g_s = growth_rate
    three_stage = (g_s - g_l) > _TWO_STAGE_GAP
    if three_stage and roe is not None:
        cap, _ = _beta.competitive_advantage_period(roe, r_disc, moat)
        plateau = max(1, round(cap / 2.0))  # 유지기 = CAP 절반
        fade_n = max(1, round(cap / 2.0))  # 감쇠기 = CAP 절반
    else:
        three_stage = False
        plateau = max(1, min(int(years), 15))  # 2단계: 명시적 구간
        fade_n = 0

    pv_explicit = 0.0
    fcf = fcf_base
    year = 0
    for _ in range(plateau):  # 유지기: g_s 유지
        year += 1
        fcf *= 1 + g_s
        pv_explicit += fcf / (1 + r_disc) ** year
    for i in range(1, fade_n + 1):  # 감쇠기(3단계만): g_s → g_l 선형
        g = g_s + (g_l - g_s) * i / fade_n
        year += 1
        fcf *= 1 + g
        pv_explicit += fcf / (1 + r_disc) ** year
    terminal_fcf = fcf * (1 + g_l)
    terminal_value = terminal_fcf / (r_disc - g_l)
    pv_terminal = terminal_value / (1 + r_disc) ** year
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
        "fcf_base_eok": fcf_base, "growth_high": round(g_s, 4), "growth_long": round(g_l, 4),
        "stages": 3 if three_stage else 2, "plateau_years": plateau, "fade_years": fade_n,
        "discount_rate": round(r_disc, 4), "net_debt_eok": nd, "shares": shares,
        "terminal_value_frac": round(pv_terminal / enterprise_value, 3) if enterprise_value else None,
    }
    # 최소 스프레드 가드로 할인율이 상향됐으면(저베타) 서술에 명시.
    disc_txt = f"{r_disc:.1%}" + (f"(저베타 최소스프레드 유계, 원 WACC {discount_rate:.1%})" if r_disc > discount_rate else "")
    if three_stage:
        r.process = [
            f"기준 FCF {_fmt(fcf_base)}억원. 3단계: 고성장 {g_s:.1%} {plateau}년 유지 → "
            f"{fade_n}년간 {g_l:.1%}로 선형 감쇠 → 영구 {g_l:.1%}",
            f"명시적 구간({year}년) 현가 합 {_fmt(pv_explicit)}억원 (할인율 {disc_txt})",
        ]
    else:
        r.process = [
            f"기준 FCF {_fmt(fcf_base)}억원, {plateau}년간 연 {g_s:.1%} 성장(2단계)",
            f"명시적 구간 현가 합 {_fmt(pv_explicit)}억원 (할인율 {disc_txt})",
        ]
    r.process += [
        f"영구성장 {g_l:.1%}(국고채 10년 기준) → 잔존가치 {_fmt(terminal_value)}억, 현가 {_fmt(pv_terminal)}억원",
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


def ggm_dcf_valuation(
    *,
    fcf_base: float | None,  # 기준연도 FCF (억원, NOPAT+D&A-CAPEX)
    growth_rate: float | None,  # 영구 성장률 (예 0.03 = 3%). WACC 초과 시 부적합.
    wacc: float | None,  # 가중평균자본비용 (예 0.09 = 9%)
    net_debt: float | None,  # 억원 (순차입 > 0, 순현금 < 0)
    shares: float | None,  # 주식수
    current_price: float | None,
) -> ValuationResult:
    """Gordon Growth Model DCF. EV = FCF1 / (WACC - g). 완전 결정론.

    단일 스테이지(영구 성장)이라 2/3단계 FCFF DCF보다 가정 민감도가 높아
    confidence='하'로 고정. WACC > g 가드로 발산 방지.
    """
    r = ValuationResult("ggm_dcf", METHOD_LABELS["ggm_dcf"], applicable=False)
    if None in (fcf_base, growth_rate, wacc, shares) or shares <= 0:
        r.note = "FCF·성장률·WACC·주식수 중 결측"
        return r
    if fcf_base <= 0:
        r.note = f"基准 FCF({_fmt(fcf_base)}억)가 0 이하 — GGM 부적합"
        return r
    if wacc <= growth_rate:
        r.note = f"WACC({wacc:.1%}) ≤ 성장률({growth_rate:.1%}) — 분모 0 이하로 발산"
        return r
    fcf1 = fcf_base * (1.0 + growth_rate)
    enterprise_value = fcf1 / (wacc - growth_rate)
    nd = net_debt or 0.0
    equity_value = enterprise_value - nd
    if equity_value <= 0:
        r.note = f"기업가치({_fmt(enterprise_value)}억) − 순차입({_fmt(nd)}억) ≤ 0"
        return r
    target = _round_won(equity_value * 1e8 / shares)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.confidence = "하"  # 단일 스테이지 — 가정 민감도 높아 고정 하
    r.assumptions = {
        "fcf_base_eok": fcf_base, "growth_rate": round(growth_rate, 4),
        "wacc": round(wacc, 4), "net_debt_eok": nd, "shares": shares,
    }
    r.process = [
        f"基准 FCF {_fmt(fcf_base)}억원, 성장률 {growth_rate:.1%}",
        f"FCF1 = {_fmt(fcf_base)} × (1+{growth_rate:.1%}) = {_fmt(fcf1)}억원",
        f"EV = {_fmt(fcf1)} ÷ ({wacc:.1%} − {growth_rate:.1%}) = {_fmt(enterprise_value)}억원",
        f"지분가치 = EV {_fmt(enterprise_value)}억 − 순차입 {_fmt(nd)}억 = {_fmt(equity_value)}억원",
        f"목표가 = {_fmt(equity_value)}억 ÷ {_fmt(shares)}주 = {_fmt(target)}원",
    ]
    return r


# ── 종목 유형별 방식 적합도(가중/제외) ────────────────────────────────────
# 밸류에이션 방식은 종목 유형에 맞춰 선택해야 한다(Damodaran story→value, CFA·McKinsey).
# fit 배수: 0=제외(부적합, blend 가중 0), 0.5=저가중, 1.0=표준, 1.5=고가중. 최종 blend 가중 =
# 신뢰도(_CONF_WEIGHT) × 이 fit. 유형 규칙에 배당·이익 게이트를 곱(min)해 무배당 DDM·적자 PER 을 제외.
_FIT_BY_TYPE: dict[str, dict[str, float]] = {
    # 성장주: 초과수익 기업 — 장부가 방식(PBR) 과소평가. PER·DCF·EV/EBITDA 우대.
    "growth": {"per": 1.5, "pbr": 0.5, "ev_ebitda": 1.0, "dcf": 1.5, "ddm": 1.0, "ggm_dcf": 1.0},
    # 자산주/가치주: 성숙·고정자산 — 장부가가 실제가치 근사. PBR 우대.
    "asset": {"per": 1.0, "pbr": 1.5, "ev_ebitda": 1.0, "dcf": 1.0, "ddm": 1.0, "ggm_dcf": 0.5},
    # 금융주(은행·보험): 부채=원재료 — EV·WACC 무의미 → EV/EBITDA·FCFF DCF 제외. DDM·P/B-ROE 우대.
    "financial": {"per": 1.0, "pbr": 1.5, "ev_ebitda": 0.0, "dcf": 0.0, "ddm": 1.5, "ggm_dcf": 0.0},
    # 시클리컬: 현재 PER 오도(사이클 역행) — 저가중. 하방서도 산출되는 EV/EBITDA 우대. DCF 정규화 전 저가중.
    "cyclical": {"per": 0.5, "pbr": 1.0, "ev_ebitda": 1.5, "dcf": 0.5, "ddm": 1.0, "ggm_dcf": 0.5},
    # 기타/일반: 중립(전부 표준 가중).
    "other": {"per": 1.0, "pbr": 1.0, "ev_ebitda": 1.0, "dcf": 1.0, "ddm": 1.0, "ggm_dcf": 0.8},
}
_ALL_METHODS = tuple(METHOD_LABELS)


# DDM 적용 최소 시가배당률(%). 이 미만이면 '유의미한 배당'이 아니라 DDM 제외 — 성장주의 상징적
# 첫 배당·미미배당(예 시가배당률 0.5%)이 배당할인모형으로 목표가를 왜곡하는 것을 막는다.
MIN_DDM_DIV_YIELD_PCT = 1.5


def method_fit(
    stock_type: str, *, div_yield_pct: float | None = None, is_loss: bool = False
) -> dict[str, float]:
    """종목 유형 → 방식별 blend 적합도 배수(0=제외~1.5=고가중). 순수 함수(재현 가능).

    stock_type: growth|asset|financial|cyclical|other. 배당·이익 게이트를 유형 규칙에 곱(min)한다:
    - 저배당(div_yield_pct < MIN_DDM_DIV_YIELD_PCT, None 포함): DDM 제외(0) — 무배당·미미배당은
      배당할인 부적합(성장주 첫 상징배당이 목표가를 끌어내리는 왜곡 방지).
    - 적자(is_loss=True): PER·DCF 제외(0) — 음의 이익으로 배수·현금흐름 붕괴(정규화 전).
    """
    base = dict(_FIT_BY_TYPE.get(stock_type, _FIT_BY_TYPE["other"]))
    if div_yield_pct is None or div_yield_pct < MIN_DDM_DIV_YIELD_PCT:
        base["ddm"] = 0.0
    if is_loss:
        base["per"] = 0.0
        base["dcf"] = 0.0
    return {m: base.get(m, 1.0) for m in _ALL_METHODS}


# ── 최종 목표가 blend ────────────────────────────────────────────────────
@dataclass
class ValuationSummary:
    """다중 밸류에이션 종합. final_target 은 적용 가능 방식의 신뢰도 가중 평균."""

    final_target: float | None
    final_upside_pct: float | None
    current_price: float | None
    method_count: int  # 적용된(계산 성공) 방식 수
    results: list[ValuationResult]


def blend(
    results: list[ValuationResult],
    current_price: float | None,
    fit_weights: dict[str, float] | None = None,
    *,
    is_growth: bool = False,
) -> ValuationSummary:
    """적용 가능 방식의 목표가를 (신뢰도 × 종목유형 적합도) 가중 평균해 최종 목표가를 낸다.

    - 신뢰도 가중(상3·중2·하1)에 fit_weights(method_fit)의 유형 적합도를 곱한다. fit=0(부적합)은
      가중 0 = 최종 평균에서 제외(예: 금융주 EV/EBITDA·DCF, 무배당 DDM). 결과 목록엔 남기고 note 표기.
    - 이상치 제외: 하방 -60%, 상방 +60%(일반) / +120%(성장주, is_growth) 초과. 성장주는 성장 반영
      방식이 후행 앵커 대비 높게 나오는 게 정상이라 상방 컷을 완화한다. fit_weights 미지정 시 기존
      신뢰도 가중만(하위호환).
    """
    fw = fit_weights or {}
    applicable = [r for r in results if r.applicable and r.target_price and r.target_price > 0]
    if not applicable:
        return ValuationSummary(None, None, current_price, 0, results)

    # 1) 유형 부적합(fit=0) 먼저 제외 — 그래야 이상치 중앙값이 부적합 방식(예: 금융주 DCF 폭주)에
    #    오염되지 않는다. 남은 적합 방식만으로 중앙값을 잡아 이상치를 판정한다.
    fitting: list[ValuationResult] = []
    for r in applicable:
        if fw.get(r.method, 1.0) <= 0:
            r.note = (r.note + " " if r.note else "") + "이 종목 유형에 부적합 — 최종 평균 제외"
        else:
            fitting.append(r)
    fitting = fitting or applicable  # 전부 부적합이면 폴백으로 전체 사용

    # 2) 적합 방식 중 이상치(중앙값 대비 초과) 제외. 성장주는 상방(성장 반영) 방식이 후행 앵커 클러스터
    #    대비 높게 나오는 게 정상이라, 상방 컷을 완화(하방은 동일)해 DCF·요인모형이 '이상치'로 잘려
    #    목표가가 후행값으로 눌리는 것을 막는다(긍정 성장 근거의 과소반영 방지).
    up_cut, down_cut = (1.2, 0.6) if is_growth else (0.6, 0.6)
    targets = sorted(r.target_price for r in fitting)  # type: ignore[misc]
    mid = targets[len(targets) // 2]  # 중앙값(적합 방식 기준)
    kept: list[ValuationResult] = []
    for r in fitting:
        dev = (r.target_price - mid) / mid if mid > 0 else 0  # type: ignore[operator]
        if dev > up_cut or -dev > down_cut:
            r.note = (r.note + " " if r.note else "") + "이상치로 최종 평균에서 제외"
        else:
            kept.append(r)
    pool = kept or fitting  # 전부 이상치면(분산 큼) 적합 방식 다 씀

    def _w(r: ValuationResult) -> float:
        return _CONF_WEIGHT.get(r.confidence, 2.0) * fw.get(r.method, 1.0)

    wsum = sum(_w(r) for r in pool)
    if wsum <= 0:  # fit 가중 폴백(전부 fit=0인 pool) — 신뢰도만으로.
        wsum = sum(_CONF_WEIGHT.get(r.confidence, 2.0) for r in pool)
        final = sum(r.target_price * _CONF_WEIGHT.get(r.confidence, 2.0) for r in pool) / wsum  # type: ignore[operator]
    else:
        final = sum(r.target_price * _w(r) for r in pool) / wsum  # type: ignore[operator]
    final_t = _round_won(final)
    return ValuationSummary(
        final_target=final_t,
        final_upside_pct=_upside(final_t, current_price),
        current_price=current_price,
        method_count=len(applicable),
        results=results,
    )
