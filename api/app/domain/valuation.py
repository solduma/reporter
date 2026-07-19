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
def _band_warning(target: float, band: dict | None, unit: str, fair_per: float | None = None) -> str:
    """LLM 목표 배수가 과거 밴드[p25,p75] 밖이면 경고 문구. clamp 하지 않고 사유만 노출(soft 가드).

    band = {median, p25, p75, n}. 밴드 없거나 유효 표본 부족(n<4)이면 경고 없음.
    환각 방지: 배수는 LLM 자유값이라 EPS(결정론) 대비 비대칭 — 과거 자기 밴드로 이상치를 투명 경고한다.
    fair_per(PEG×장기성장 정당 PER)가 있으면 상회 경고에 함께 노출 — 장기성장이면 정당 PER 이 밴드를
    넘는 게 정상이므로, 목표배수가 정당 PER 이내면 리레이팅이 성장으로 정당화됨을 알린다.
    """
    fair_txt = f" [PEG 기준 정당 PER {fair_per:g}배]" if fair_per else ""
    if not band or band.get("n", 0) < 4:
        return ""
    p25, p75, med = band.get("p25"), band.get("p75"), band.get("median")
    if p25 is None or p75 is None:
        return ""
    yrs = f"{band['n']}개 분기"
    if target > p75:
        rerate = ""
        if fair_per and target <= fair_per:
            rerate = " — 단, 장기성장 기준 정당 PER 이내라 리레이팅이 성장으로 정당화됨"
        return (f"⚠ 목표 {unit} {target:g}배는 과거 밴드(중앙값 {med:g}, {p25:g}~{p75:g}배, {yrs})를 "
                f"상회{fair_txt} — 성장 가속 등 리레이팅 근거 필요{rerate}")
    if target < p25:
        return (f"⚠ 목표 {unit} {target:g}배는 과거 밴드(중앙값 {med:g}, {p25:g}~{p75:g}배, {yrs})를 "
                f"하회 — 디레이팅 근거 필요")
    return ""


def per_valuation(
    *, forward_eps: float | None, target_per: float | None, current_price: float | None,
    per_band: dict | None = None, fair_per: float | None = None, per_source: str = "",
) -> ValuationResult:
    """목표가 = 예상 EPS × 목표 PER. 성장주·이익 창출 기업의 기본.

    forward_eps·target_per 는 결정론적으로 산출돼 들어온다(외삽·HITL EPS, PEG 정당 PER). per_source 는
    목표배수 출처(예 'PEG 정당 PER'·'과거 밴드 중앙값') — process 에 노출해 재현성을 투명화한다.
    per_band 가 있으면 목표 PER 이 밴드[p25,p75] 밖일 때 경고를, fair_per 로 리레이팅 정당성을 안내한다(soft).
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
    r.note = _band_warning(target_per, per_band, "PER", fair_per)
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
    risk_free: float | None = None,  # 있으면 영구성장 상한 = min(GDP캡, rf)
) -> ValuationResult:
    """FCFF DCF. 고성장주는 3단계(고성장 유지→선형 감쇠→영구), 완만성장주는 2단계로 자동 선택.

    Damodaran/CFA/McKinsey: 2단계의 '즉시 영구 전환'은 고성장주를 왜곡하므로, 성장률이 안정률보다
    8%p 초과면 전환기를 둔 3단계(FF/APT 와 동일 CAP·선형 감쇠)를 쓴다. 영구성장은 rf/경제성장으로
    상한(성장은 ROIC>WACC 일 때만 가치 — 과도한 터미널 성장 방지)."""
    from app.domain import beta as _beta

    r = ValuationResult("dcf", METHOD_LABELS["dcf"], applicable=False)
    if None in (fcf_base, growth_rate, terminal_growth, discount_rate, shares) or shares <= 0:
        r.note = "FCF·성장률·영구성장률·할인율·주식수 중 결측"
        return r
    # 영구성장 상한: 경제성장(GDP캡)·무위험수익률 이하(Damodaran). 발산·과대 방지.
    g_l = min(terminal_growth, _beta.TERMINAL_GROWTH_CAP)
    if risk_free is not None:
        g_l = min(g_l, risk_free)
    if discount_rate <= g_l:
        r.note = f"할인율({discount_rate:.1%}) ≤ 영구성장률({g_l:.1%}) — 잔존가치 발산"
        return r
    if fcf_base <= 0:
        r.note = f"기준 FCF({_fmt(fcf_base)}억)가 0 이하 — DCF 부적합"
        return r

    # 성장 프로필로 2단계/3단계 선택. 고성장(격차>8%p)이면 CAP 기반 3단계.
    g_s = min(growth_rate, _beta.NEAR_TERM_GROWTH_CAP)
    three_stage = (g_s - g_l) > _TWO_STAGE_GAP
    if three_stage and roe is not None:
        cap, _ = _beta.competitive_advantage_period(roe, discount_rate, moat)
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
        pv_explicit += fcf / (1 + discount_rate) ** year
    for i in range(1, fade_n + 1):  # 감쇠기(3단계만): g_s → g_l 선형
        g = g_s + (g_l - g_s) * i / fade_n
        year += 1
        fcf *= 1 + g
        pv_explicit += fcf / (1 + discount_rate) ** year
    terminal_fcf = fcf * (1 + g_l)
    terminal_value = terminal_fcf / (discount_rate - g_l)
    pv_terminal = terminal_value / (1 + discount_rate) ** year
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
        "discount_rate": discount_rate, "net_debt_eok": nd, "shares": shares,
        "terminal_value_frac": round(pv_terminal / enterprise_value, 3) if enterprise_value else None,
    }
    if three_stage:
        r.process = [
            f"기준 FCF {_fmt(fcf_base)}억원. 3단계: 고성장 {g_s:.1%} {plateau}년 유지 → "
            f"{fade_n}년간 {g_l:.1%}로 선형 감쇠 → 영구 {g_l:.1%}",
            f"명시적 구간({year}년) 현가 합 {_fmt(pv_explicit)}억원 (할인율 {discount_rate:.1%})",
        ]
    else:
        r.process = [
            f"기준 FCF {_fmt(fcf_base)}억원, {plateau}년간 연 {g_s:.1%} 성장(2단계)",
            f"명시적 구간 현가 합 {_fmt(pv_explicit)}억원 (할인율 {discount_rate:.1%})",
        ]
    r.process += [
        f"영구성장 {g_l:.1%}(rf/GDP 상한) → 잔존가치 {_fmt(terminal_value)}억, 현가 {_fmt(pv_terminal)}억원",
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


def _three_stage_pe(
    g_s: float, g_l: float, plateau: float, fade: float, r_growth: float, r_term: float
) -> tuple[float, float]:
    """3단계(고성장 유지→선형감쇠→영구) 내재 목표 PER(forward EPS=1 기준)과 터미널가치 비중.

    - 유지기(plateau 년): 이익을 g_s 로 복리, 성장국면 할인율 r_growth 로 현가.
    - 감쇠기(fade 년): g 가 g_s→g_l 로 선형 감쇠, 계속 r_growth 로 현가.
    - 터미널: 마지막 EPS 에 고든 PER=1/(r_term − g_l) 적용 후 성장국면 할인율로 현가.
    forward EPS=1 이므로 현가 합이 곧 목표 PER. plateau·fade 는 정수 연수로 반올림해 명시적 누적.
    """
    eps = 1.0
    pv = 0.0
    year = 0
    for _ in range(max(1, round(plateau))):  # 유지기: g_s 유지
        year += 1
        eps *= 1 + g_s
        pv += eps / (1 + r_growth) ** year
    n_fade = max(1, round(fade))
    for i in range(1, n_fade + 1):  # 감쇠기: g_s → g_l 선형
        g = g_s + (g_l - g_s) * i / n_fade
        year += 1
        eps *= 1 + g
        pv += eps / (1 + r_growth) ** year
    terminal_pe = 1.0 / (r_term - g_l)
    tv = eps * (1 + g_l) * terminal_pe  # 터미널 EPS × 고든 PER
    pv_tv = tv / (1 + r_growth) ** year
    pv += pv_tv
    return pv, (pv_tv / pv if pv > 0 else 0.0)


def _factor_model_valuation(
    method: str,
    *,
    forward_eps: float | None,
    risk_free: float | None,
    factors: list[FactorExposure],
    earnings_growth: float | None,  # 명목 이익 성장률(단기 고성장 g_S).
    equity_value: float | None = None,  # 시총(억원) — 터미널 WACC 자본가중
    net_debt: float | None = None,  # 순차입(억원) — 터미널 WACC 부채가중
    roe: float | None = None,  # ROE(초과수익 → CAP 지속성 기준선)
    moat: str | None = None,  # 해자 판정(강|중|약, LLM) → CAP 기준연수
    current_price: float | None = None,
) -> ValuationResult:
    """요인모형(Fama-French/APT) 3단계 목표가. 국면별 할인율로 저베타 성장주 저평가·FF=APT 동일값 해소.

    저베타 이상현상(Frazzini-Pedersen 2014)에서 총 Re 를 8.2% 로 clamp 하면 FF·APT 가 둘 다 눌려
    동일해지고 성장주가 저평가됐다(딥리서치 2건). 그래서:
    - **성장국면 할인율** = 요인 Re(하한 없음, rf+2% 완만한 하한만 — 극단 저베타 PER 폭주 완화).
      하한을 성장국면 총 Re 가 아니라 완만하게만 걸어 FF·APT 차등을 보존한다.
    - **터미널 할인율** = β→1 수렴(Damodaran) WACC, 단 (r−g_L) 최소 스프레드로 목표 PER 폭발 방지.
    - **3단계 성장**: 고성장 유지(CAP/2) → 선형 감쇠(CAP/2) → 영구(g_L≤rf). H-Model 이 없앤 유지구간 복원.
    """
    from app.domain import beta as _beta

    r = ValuationResult(method, METHOD_LABELS[method], applicable=False)
    if forward_eps is None or risk_free is None or earnings_growth is None or not factors:
        r.note = "예상 EPS·무위험수익률·이익성장률·요인노출 중 결측"
        return r
    if forward_eps <= 0:
        r.note = f"예상 EPS({_fmt(forward_eps)})가 0 이하 — 요인모형 PER 적용 불가"
        return r
    # 1) 요인 Re. 성장국면 할인율 = raw Re, 단 완만한 하한(rf+2%)만(FF·APT 차등 보존, 폭주 완화).
    re_raw, ret_steps = _required_return(risk_free, factors)
    growth_floor = risk_free + _beta.GROWTH_FLOOR_PREMIUM
    r_growth = max(re_raw, growth_floor)
    if r_growth > re_raw:
        ret_steps.append(f"→ 성장국면 완만한 하한 {r_growth:.1%} (rf+{_beta.GROWTH_FLOOR_PREMIUM:.0%}, 극단 저베타 완화)")
    # 2) 터미널 할인율: 성숙기 β→1 수렴(Damodaran). 시장 Re = rf + 시장프리미엄(첫 요인=시장).
    market_premium = factors[0].premium if factors else _beta.MARKET_PREMIUM
    re_terminal = risk_free + 1.0 * market_premium  # β→1
    if equity_value:
        r_term, wacc_steps = _beta.wacc(re_terminal, equity_value, net_debt, risk_free)
    else:
        r_term, wacc_steps = re_terminal, [f"터미널 할인율 = 시장 Re {re_terminal:.1%}(β→1, 시총 미상 WACC 생략)"]
    # 3) 성장률·CAP. g_L ≤ min(GDP캡, rf)(Damodaran). 터미널 (r−g_L) 최소 스프레드로 PER 폭발 방지.
    g_s = min(earnings_growth, _beta.NEAR_TERM_GROWTH_CAP)
    g_l = min(_beta.TERMINAL_GROWTH_CAP, risk_free)
    r_term = max(r_term, g_l + _beta.MIN_TERM_SPREAD)
    cap, cap_steps = _beta.competitive_advantage_period(roe, r_term, moat)
    plateau = round(cap / 2.0, 1)  # 유지기 = CAP 절반
    fade = round(cap / 2.0, 1)  # 감쇠기 = CAP 절반
    target_per, tv_frac = _three_stage_pe(g_s, g_l, plateau, fade, r_growth, r_term)
    target = _round_won(forward_eps * target_per)
    r.applicable = True
    r.target_price = target
    r.upside_pct = _upside(target, current_price)
    r.confidence = "하"  # 요인·프리미엄 추정 불확실성이 커 기본 낮게
    r.assumptions = {
        "forward_eps": forward_eps, "risk_free": risk_free,
        "discount_growth": round(r_growth, 4), "discount_terminal": round(r_term, 4),
        "growth_high": round(g_s, 4), "growth_long": round(g_l, 4),
        "cap_years": cap, "plateau_years": plateau, "fade_years": fade,
        "moat": moat, "roe": roe, "terminal_value_frac": round(tv_frac, 3),
        "factors": [{"name": f.name, "beta": f.beta, "premium": f.premium} for f in factors],
        "implied_target_per": round(target_per, 2),
    }
    r.process = [
        *ret_steps, *wacc_steps, *cap_steps,
        f"3단계: 고성장 {g_s:.1%} {plateau:g}년 유지 → {fade:g}년간 {g_l:.1%}로 선형 감쇠 → 영구 {g_l:.1%}",
        f"할인율: 성장국면 {r_growth:.1%} · 터미널 {r_term:.1%}(β→1, 터미널가치 비중 {tv_frac:.0%})",
        f"내재 목표 PER = {target_per:.1f}배 (터미널 PER=1/({r_term:.1%}−{g_l:.1%}))",
        f"목표가 = 예상 EPS {_fmt(forward_eps)} × {target_per:.1f} = {_fmt(target)}원",
    ]
    return r


def fama_french_valuation(**kwargs) -> ValuationResult:
    """Fama-French 3요인(시장·규모SMB·가치HML) 요구수익률 → 목표 PER → 목표가."""
    return _factor_model_valuation("fama_french", **kwargs)


def apt_valuation(**kwargs) -> ValuationResult:
    """APT: 임의 다요인(금리·경기·인플레·환율 등) 요구수익률 → 목표 PER → 목표가."""
    return _factor_model_valuation("apt", **kwargs)


# ── 종목 유형별 방식 적합도(가중/제외) ────────────────────────────────────
# 밸류에이션 방식은 종목 유형에 맞춰 선택해야 한다(Damodaran story→value, CFA·McKinsey).
# fit 배수: 0=제외(부적합, blend 가중 0), 0.5=저가중, 1.0=표준, 1.5=고가중. 최종 blend 가중 =
# 신뢰도(_CONF_WEIGHT) × 이 fit. 유형 규칙에 배당·이익 게이트를 곱(min)해 무배당 DDM·적자 PER 을 제외.
_FIT_BY_TYPE: dict[str, dict[str, float]] = {
    # 성장주: 초과수익 기업 — 장부가 방식(PBR·자산가치) 과소평가. PER·DCF·EV/EBITDA·요인모형 우대.
    "growth": {"per": 1.5, "pbr": 0.5, "ev_ebitda": 1.0, "dcf": 1.5,
               "ddm": 1.0, "asset": 0.5, "fama_french": 1.0, "apt": 1.0},
    # 자산주/가치주: 성숙·고정자산 — 장부가가 실제가치 근사. 자산가치·PBR 우대.
    "asset": {"per": 1.0, "pbr": 1.5, "ev_ebitda": 1.0, "dcf": 1.0,
              "ddm": 1.0, "asset": 1.5, "fama_french": 1.0, "apt": 1.0},
    # 금융주(은행·보험): 부채=원재료 — EV·WACC 무의미 → EV/EBITDA·FCFF DCF 제외. DDM·P/B-ROE 우대.
    "financial": {"per": 1.0, "pbr": 1.5, "ev_ebitda": 0.0, "dcf": 0.0,
                  "ddm": 1.5, "asset": 1.0, "fama_french": 1.0, "apt": 1.0},
    # 시클리컬: 현재 PER 오도(사이클 역행) — 저가중. 하방서도 산출되는 EV/EBITDA 우대. DCF 정규화 전 저가중.
    "cyclical": {"per": 0.5, "pbr": 1.0, "ev_ebitda": 1.5, "dcf": 0.5,
                 "ddm": 1.0, "asset": 1.0, "fama_french": 1.0, "apt": 1.0},
    # 기타/일반: 중립(전부 표준 가중).
    "other": {"per": 1.0, "pbr": 1.0, "ev_ebitda": 1.0, "dcf": 1.0,
              "ddm": 1.0, "asset": 1.0, "fama_french": 1.0, "apt": 1.0},
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
