"""딥다이브 5단계 Valuation — 8개 방식을 에이전틱 도구호출로 종합해 최종 목표가를 낸다.

역할 분리(hexagonal):
- **판단(가정)**은 LLM: 예상 EPS·목표 멀티플·성장률·할인율·베타·요인 프리미엄 등을 근거와 함께 제시.
- **산식·목표가·과정 서술**은 domain.valuation(순수·재현 가능)이 소유 — 환각 없는 결정론적 계산.

에이전틱 루프(chat_tools 네이티브 도구호출):
LLM 이 get_anchors 로 실데이터를 읽고 → compute_* 도구로 방식별 목표가를 계산해 **결과를 직접 보고**
→ 방식 간 목표가가 크게 어긋나면 가정을 고쳐 재계산 → blend 로 최종 목표가 확인 → finalize 로 마무리.
원샷 blob 추출과 달리 LLM 이 계산 결과를 피드백받아 스스로 검증·수정한다. 계산은 코드가 소유하므로
목표가는 여전히 결정론적. 도구 실행 결과(성공/실패 사유)를 매 턴 주입해 자기수정을 유도한다.
"""

from __future__ import annotations

import json
import logging

from app.domain import beta as betamod
from app.domain import forward as fwd
from app.domain import valuation as val
from app.ports.llm import LLMError, LLMPort
from app.services import company_service
from app.services.deepdive.tools import ToolContext, dispatch, sector_for

logger = logging.getLogger(__name__)

_INDEX_SYMBOL = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}


def compute_factor_betas(ctx: ToolContext, anchors: dict, market: str | None) -> dict:
    """실데이터 요인 베타. 시장베타는 지수·개별주 일봉 회귀, SMB/HML 은 시총·PBR 프록시.

    프리미엄·무위험수익률은 domain.beta 상수(한국 시장 관례). LLM 추정 대신 재현 가능한 실측값."""
    idx_sym = _INDEX_SYMBOL.get((market or "").upper())
    market_beta = None
    if idx_sym:
        stock_closes = company_service.daily_closes(ctx.db, ctx.code)
        index_closes = company_service.daily_closes(ctx.db, idx_sym)
        market_beta = betamod.market_beta(stock_closes, index_closes)
    if market_beta is None:
        market_beta = 1.0  # 회귀 불가(일봉 부족·지수 없음) 시 시장 평균 베타로 보수적 근사
    return {
        "market_beta": market_beta,
        "smb_beta": betamod.smb_beta(anchors.get("market_cap_eok")),
        "hml_beta": betamod.hml_beta(anchors.get("current_pbr")),
        "risk_free": betamod.RISK_FREE,
        "market_premium": betamod.MARKET_PREMIUM,
        "smb_premium": betamod.SMB_PREMIUM,
        "hml_premium": betamod.HML_PREMIUM,
        "beta_source": "회귀(지수 일봉)" if idx_sym and market_beta != 1.0 else "근사(1.0)",
    }


# ── 숫자 유틸 ────────────────────────────────────────────────────────────
def _num(x) -> float | None:
    """관대 숫자 변환('12.3'·12·None → float|None). 파싱 불가면 None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _period_key(period: str) -> tuple[int, int] | None:
    """'YYYY.MM' → (year, month). 추정치 '(E)' 포함해도 앞 6자만 파싱. 실패 시 None."""
    if not period or len(period) < 7 or period[4] != ".":
        return None
    try:
        return int(period[:4]), int(period[5:7])
    except ValueError:
        return None


# ── 앵커(실데이터) ───────────────────────────────────────────────────────
def _sorted_actuals(series: list[dict]) -> list[dict]:
    """실적(비추정) 행을 기간 오름차순 정렬. period 파싱 실패 행은 제외."""
    rows = [r for r in series if not r.get("is_estimate") and _period_key(r.get("period", ""))]
    return sorted(rows, key=lambda r: _period_key(r["period"]))  # type: ignore[arg-type,index]


def _latest_annual(rows: list[dict], field: str) -> float | None:
    """연간(.12) 행 중 field 최신 유효값. 연간 데이터가 정확한 지표(EBITDA·배당)에 쓴다."""
    for r in reversed(rows):
        if _period_key(r["period"])[1] == 12:  # type: ignore[index]
            v = _num(r.get(field))
            if v is not None:
                return v
    return None


def _latest_pointintime(rows: list[dict], field: str) -> float | None:
    """시점값(BPS·PER·PBR 등) 최신 유효값. 분기·연간 무관 가장 최근."""
    for r in reversed(rows):
        v = _num(r.get(field))
        if v is not None:
            return v
    return None


def _ebitda_to_eok(ebitda: float, revenue: float | None) -> float:
    """EBITDA 억원 정규화(2차 방어). DB 정규화 마이그레이션이 근본 수정이나, 배치 지연·구 데이터
    대비 읽기 시점에도 방어한다. EBITDA 마진 |ebitda/revenue|>1e4 면 원 단위로 보고 /1e8."""
    if revenue and revenue > 0:
        return ebitda / 1e8 if abs(ebitda / revenue) > 1e4 else ebitda
    return ebitda / 1e8 if abs(ebitda) > 1e7 else ebitda


def _latest_annual_ebitda_eok(rows: list[dict]) -> float | None:
    """연간(.12) 최신 EBITDA 를 억원으로 정규화해 반환(단위 혼재 2차 방어)."""
    for r in reversed(rows):
        if _period_key(r["period"])[1] == 12:  # type: ignore[index]
            v = _num(r.get("ebitda"))
            if v is not None:
                return _ebitda_to_eok(v, _num(r.get("revenue")))
    return None


def _per_band(rows: list[dict], window: int = 40) -> dict | None:
    """과거 PER 밴드 = 최근 window 분기(기본 40=10년) 중 양수 PER 의 중앙값·p25·p75.

    목표 배수(LLM 자유값)의 soft 가드 기준선. 긴 창인 이유: 조선 등 초장기 사이클 산업의 정상 밴드를
    잡으려면 긴 히스토리가 필요. 사분위 경계라 프로덕트 믹스 변화·이상치는 어느정도 상쇄된다.
    적자 분기(음수 PER)는 무의미해 제외. 유효 표본 4개 미만이면 밴드 신뢰 불가 → None(가드 스킵).
    """
    pers = [_num(r.get("per")) for r in rows[-window:]]
    pers = sorted(v for v in pers if v is not None and v > 0)
    n = len(pers)
    if n < 4:
        return None

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, round(p * (n - 1))))
        return round(pers[idx], 1)

    return {"median": _pct(0.5), "p25": _pct(0.25), "p75": _pct(0.75), "n": n}


def _ttm_eps(rows: list[dict]) -> float | None:
    """주당순이익 TTM(최근 4개 분기 EPS 합). 이 프로젝트 EPS 는 분기 개별값이라(.12=Q4 포함)
    분기값에 연간 목표 PER 을 곱하면 ~4배 과소평가된다 → 반드시 최근 4분기를 합해 연환산한다.

    4개 분기 미만이면 TTM 신뢰 불가 → None(LLM forward_eps 에 의존). period 는 모두 분기로 본다.
    """
    eps = [_num(r.get("eps")) for r in rows]
    eps = [v for v in eps if v is not None]
    if len(eps) >= 4:
        return sum(eps[-4:])
    return None


def _ttm_windows(rows: list[dict], field: str) -> list[float]:
    """분기 시계열에서 4분기 롤링 합(TTM) 목록. 시클리컬 정규화용(연환산 이익·매출 창)."""
    vals = [_num(r.get(field)) for r in rows]
    out: list[float] = []
    for i in range(3, len(vals)):
        window = vals[i - 3 : i + 1]
        if all(v is not None for v in window):
            out.append(sum(window))  # type: ignore[arg-type]
    return out


def _normalized_eps(rows: list[dict], ttm_eps: float | None) -> tuple[float | None, dict | None]:
    """시클리컬 정규화 EPS = TTM EPS × (중간사이클 순마진 / 현재 TTM 순마진).

    시클리컬은 마진이 사이클과 함께 크게 출렁여(고점 과대·저점 과소) 현재 TTM 이익이 기준연도로
    부적합하다(Damodaran mid-cycle). 과거 사이클의 TTM 순마진 중앙값을 '정상 마진'으로 잡고 현재
    마진과의 비율로 EPS 를 보정한다. 최소 6개 TTM 창(≈1.5년치 분기)·양수 현재마진일 때만. 실패 시 None.
    """
    if ttm_eps is None:
        return None, None
    ni = _ttm_windows(rows, "net_income")
    rev = _ttm_windows(rows, "revenue")
    n = min(len(ni), len(rev))
    if n < 6:
        return None, None  # 사이클 판단할 히스토리 부족
    margins = [ni[-n + i] / rev[-n + i] for i in range(n) if rev[-n + i] > 0]
    if len(margins) < 6:
        return None, None
    mid_margin = sorted(margins)[len(margins) // 2]  # 중앙값(정상 마진)
    current_margin = margins[-1]
    if current_margin <= 0 or mid_margin <= 0:
        return None, None
    norm_eps = ttm_eps * (mid_margin / current_margin)
    meta = {
        "normalized_eps": round(norm_eps, 1), "ttm_eps": round(ttm_eps, 1),
        "mid_cycle_margin": round(mid_margin, 4), "current_margin": round(current_margin, 4),
        "cycle_quarters": n,
    }
    return norm_eps, meta


def _consensus_eps_ttm(series: list[dict]) -> float | None:
    """컨센서스 추정 EPS 로 forward TTM 근사 = 추정행 EPS 합(연환산). 추정 EPS 없으면 None.

    네이버 (E) 행에 EPS 가 담긴 극소수 종목만 잡힌다(대부분 결측). 분기 추정이면 4개 합, 연간 추정
    (.12(E))이면 그 값 자체가 연환산. 표기가 섞일 수 있어 추정행 EPS 를 모아 최대 4개까지 합한다.
    """
    est_eps = [_num(r.get("eps")) for r in series if r.get("is_estimate")]
    est_eps = [v for v in est_eps if v is not None]
    if not est_eps:
        return None
    return sum(est_eps[-4:])


def apply_forward_earnings(anchors: dict, series: list[dict]) -> dict:
    """이익 앵커(eps_ttm·ebitda_eok_annual)를 forward(예상)로 대체. 소스 우선순위로 성장률을 정한다.

    우선순위:
        (1) HITL — apply_hitl_to_anchors 가 이미 반영(hitl_earnings_uplift). 여기선 건드리지 않는다.
        (2) 컨센서스 — 추정 EPS 가 있으면 그 TTM 을 forward EPS 로, 성장률은 컨센서스/현재 TTM 으로 역산.
        (3) 성장률 외삽 — domain.forward 앙상블 성장률(과거3년평균·최근·convex)로 이익을 1년 전방 투영.

    forward_meta[metric] = {source, growth_pct, base, forward, components?} 로 고지한다. 시클리컬
    정규화 EPS 는 run_valuation 이 이 함수 뒤에 별도 대체하므로 여기서 eps 를 외삽해도 덮어써진다.
    HITL uplift 가 이미 적용됐으면(hitl_earnings_uplift 존재) 이익은 그대로 두고 성장률 외삽을 생략한다.
    """
    rows = _sorted_actuals(series)
    adjusted = dict(anchors)
    meta: dict = {}

    if anchors.get("hitl_earnings_uplift"):
        meta["source"] = "hitl"  # (1) HITL 이 이미 이익 앵커를 상향 — 외삽으로 덮지 않는다.
        adjusted["forward_meta"] = meta
        return adjusted

    # (2) 컨센서스 추정 EPS 우선.
    consensus = _consensus_eps_ttm(series)
    base_eps = anchors.get("eps_ttm")
    if consensus is not None and consensus > 0:
        adjusted["eps_ttm"] = round(consensus, 2)
        g = round(consensus / base_eps - 1.0, 4) if base_eps and base_eps > 0 else None
        meta["eps"] = {"source": "consensus", "base_ttm": base_eps,
                       "forward": round(consensus, 2), "growth_pct": g * 100 if g is not None else None}
        # 컨센서스 성장률을 EBITDA 에도 동일 적용(추정 EBITDA 는 없으므로 이익 성장 프록시).
        if g is not None:
            _apply_growth_to_ebitda(adjusted, meta, g, source="consensus")
        adjusted["forward_meta"] = meta
        return adjusted

    # (3) 성장률 외삽. EPS 는 자체 TTM 시계열로(주식수 변동 반영), EBITDA 는 자체 TTM 시계열이 없어
    # 순이익 성장을 프록시로 쓴다.
    eps_growth, eps_gmeta = fwd.extrapolate_growth(_ttm_windows(rows, "eps"))
    if base_eps is not None and eps_growth is not None:
        adjusted["eps_ttm"] = round(base_eps * (1.0 + eps_growth), 2)
        meta["eps"] = {"source": "extrapolation", "base_ttm": base_eps,
                       "forward": round(base_eps * (1.0 + eps_growth), 2), **eps_gmeta}
    ni_growth, ni_gmeta = fwd.extrapolate_growth(_ttm_windows(rows, "net_income"))
    if ni_growth is not None:
        _apply_growth_to_ebitda(adjusted, meta, ni_growth, source="extrapolation", gmeta=ni_gmeta)
    if meta:
        adjusted["forward_meta"] = meta
    return adjusted


def _apply_growth_to_ebitda(anchors: dict, meta: dict, growth: float, *, source: str, gmeta: dict | None = None) -> None:
    """이익 성장률을 EBITDA 앵커에 동일 적용(추정 EBITDA 소스가 없어 이익 성장을 프록시로 씀)."""
    base = anchors.get("ebitda_eok_annual")
    if base is None:
        return
    fwd_val = round(base * (1.0 + growth), 2)
    anchors["ebitda_eok_annual"] = fwd_val
    meta["ebitda"] = {"source": source, "base_annual": base, "forward": fwd_val,
                      "growth_pct": round(growth * 100, 2), **(gmeta or {})}


def collect_anchors(series: list[dict], price: dict) -> dict:
    """밸류에이션 실데이터 앵커. 기간 granularity 를 구분해 연환산·시점값을 올바르게 뽑는다.

    - eps: TTM(분기 EPS 4개 합 또는 연간). bps: 최신 시점값. ebitda/dps: 연간(.12).
    - shares = 시총/현재가. net_debt = ebitda×ev_ebitda − 시총(억원, EV 역산). 셋 다 있을 때만.
    """
    rows = _sorted_actuals(series)
    current_price = _num(price.get("close_price"))
    market_cap = _num(price.get("market_cap"))  # 원
    eps_ttm = _ttm_eps(rows)
    bps = _latest_pointintime(rows, "bps")
    ebitda = _latest_annual_ebitda_eok(rows)  # 억원 정규화(원·억원 혼재 2차 방어)
    dps = _latest_annual(rows, "dps")  # 원, 연간
    ev_ebitda = _latest_pointintime(rows, "ev_ebitda")

    shares = None
    if market_cap and current_price and current_price > 0:
        shares = market_cap / current_price
    net_debt = None  # 억원. 연간 EBITDA·최신 EV/EBITDA·시총 모두 있을 때만 역산(불완전하면 미제공).
    if ebitda and ev_ebitda and market_cap:
        net_debt = ebitda * ev_ebitda - market_cap / 1e8

    return {
        "current_price": current_price,
        "market_cap_eok": market_cap / 1e8 if market_cap else None,
        "eps_ttm": eps_ttm, "bps": bps, "ebitda_eok_annual": ebitda, "dps_annual": dps,
        "current_per": _latest_pointintime(rows, "per"),
        "per_band": _per_band(rows),  # 과거 10년 PER 밴드 — 목표배수 soft 가드 기준선
        "current_pbr": _latest_pointintime(rows, "pbr"),
        "current_ev_ebitda": ev_ebitda,
        "div_yield_pct": _latest_pointintime(rows, "div_yield"),
        "roe_pct": _latest_pointintime(rows, "roe"),  # H-Model 감쇠기간 정량 기준선
        "shares": shares, "net_debt_eok": net_debt,
    }


# HITL 이익 증분의 앵커 반영 상한 — 단일 인풋이 forward 이익을 과대 상향(환각)하지 않도록 캡(+50%).
_HITL_EARNINGS_UPLIFT_CAP = 0.5


def apply_hitl_to_anchors(anchors: dict, hitl: dict | None) -> dict:
    """HITL numeric claim 의 전사 이익 증분을 forward 이익 앵커(eps_ttm·ebitda)에 결정론적으로 반영.

    긍정 인풋이 '프롬프트 텍스트'로만 전달돼 LLM 이 forward 를 안 올리면 후행 앵커로 계산돼 목표가에
    반영되지 않던 문제(업사이드 왜곡) 대응. numeric 필드가 구조화(delta_pct·segment_revenue_share)돼
    있고 확률 가중 후 전사 증분을 계산할 수 있는 claim 만 적용한다:
        전사 이익 증분율 = delta_pct/100 × segment_revenue_share/100 × probability  (반박=prob0 → 0)
    합산 증분율을 상한(_HITL_EARNINGS_UPLIFT_CAP)으로 캡해 eps_ttm·ebitda 앵커에 곱한다. 조정 근거는
    anchors['hitl_earnings_uplift'] 로 남겨 프론트·서술이 투명하게 노출한다. baseline/비중이 없어 증분을
    못 구하는 claim 은 여기서 건너뛰고 기존 프롬프트 경로(LLM 판단)에 맡긴다.
    """
    if not hitl or not isinstance(hitl, dict):
        return anchors
    total_uplift = 0.0
    applied: list[dict] = []
    for c in hitl.get("claims") or []:
        if not isinstance(c, dict) or c.get("claim_type") != "numeric":
            continue
        num = c.get("numeric") if isinstance(c.get("numeric"), dict) else None
        if not num:
            continue
        delta_pct = _num(num.get("delta_pct"))
        share_pct = _num(num.get("segment_revenue_share"))
        prob = _num(c.get("probability"))
        if delta_pct is None or share_pct is None or prob is None:
            continue  # 구조화 증분 불가 — 프롬프트 경로에 위임
        contrib = (delta_pct / 100.0) * (share_pct / 100.0) * prob
        if contrib <= 0:
            continue
        total_uplift += contrib
        applied.append({"claim": c.get("claim"), "contrib_pct": round(contrib * 100, 2)})
    if not applied:
        return anchors
    uplift = min(total_uplift, _HITL_EARNINGS_UPLIFT_CAP)
    adjusted = dict(anchors)
    factor = 1.0 + uplift
    if adjusted.get("eps_ttm") is not None:
        adjusted["eps_ttm"] = round(adjusted["eps_ttm"] * factor, 2)
    if adjusted.get("ebitda_eok_annual") is not None:
        adjusted["ebitda_eok_annual"] = round(adjusted["ebitda_eok_annual"] * factor, 2)
    adjusted["hitl_earnings_uplift"] = {
        "uplift_pct": round(uplift * 100, 2),
        "capped": total_uplift > _HITL_EARNINGS_UPLIFT_CAP,
        "claims": applied,
    }
    return adjusted


# ── 컴퓨트 도구(순수 domain 래퍼) ────────────────────────────────────────
# 각 도구: LLM 이 준 가정 args + 앵커(anchors)로 domain.valuation 호출 → ValuationResult dict.
# forward 값이 없으면 앵커로 폴백하되, 0/음수도 명시적으로 존중(_pick 이 None 만 폴백).
def _pick(arg, anchor):
    """arg 가 주어졌으면(0·음수 포함) 그 값, 아니면 anchor. None 만 폴백."""
    v = _num(arg)
    return v if v is not None else anchor


def _t_per(a: dict, anc: dict) -> val.ValuationResult:
    return val.per_valuation(
        forward_eps=_pick(a.get("forward_eps"), anc.get("eps_ttm")),
        target_per=_num(a.get("target_per")), current_price=anc.get("current_price"),
        per_band=anc.get("per_band"),
    )


def _t_pbr(a: dict, anc: dict) -> val.ValuationResult:
    return val.pbr_valuation(
        bps=_pick(a.get("bps"), anc.get("bps")),
        target_pbr=_num(a.get("target_pbr")), current_price=anc.get("current_price"),
    )


def _t_ev_ebitda(a: dict, anc: dict) -> val.ValuationResult:
    return val.ev_ebitda_valuation(
        forward_ebitda=_pick(a.get("forward_ebitda_eok"), anc.get("ebitda_eok_annual")),
        target_ev_ebitda=_num(a.get("target_ev_ebitda")),
        net_debt=_pick(a.get("net_debt_eok"), anc.get("net_debt_eok")),
        shares=anc.get("shares"), current_price=anc.get("current_price"),
    )


def _t_dcf(a: dict, anc: dict) -> val.ValuationResult:
    # roe·moat 로 고성장주는 3단계(CAP 기반 전환기), 완만성장주는 2단계 자동 선택. risk_free 로 영구성장 상한.
    return val.dcf_valuation(
        fcf_base=_num(a.get("fcf_base_eok")), growth_rate=_num(a.get("growth_rate")),
        years=int(_num(a.get("years")) or 5), terminal_growth=_num(a.get("terminal_growth")),
        discount_rate=_num(a.get("discount_rate")),
        net_debt=_pick(a.get("net_debt_eok"), anc.get("net_debt_eok")),
        shares=anc.get("shares"), current_price=anc.get("current_price"),
        roe=anc.get("roe_pct"), moat=anc.get("moat"),
        risk_free=(anc.get("factor_betas") or {}).get("risk_free"),
    )


def _t_ddm(a: dict, anc: dict) -> val.ValuationResult:
    return val.ddm_valuation(
        dps=_pick(a.get("dps"), anc.get("dps_annual")),
        dividend_growth=_num(a.get("dividend_growth")), cost_of_equity=_num(a.get("cost_of_equity")),
        current_price=anc.get("current_price"),
    )


def _t_asset(a: dict, anc: dict) -> val.ValuationResult:
    return val.asset_valuation(
        book_equity_per_share=_pick(a.get("book_equity_per_share"), anc.get("bps")),
        asset_premium=_num(a.get("asset_premium")), current_price=anc.get("current_price"),
    )


def _t_fama_french(a: dict, anc: dict) -> val.ValuationResult:
    # 베타·프리미엄은 실데이터(anchors.factor_betas: 지수회귀 시장베타 + 시총/PBR 프록시 + 관례 프리미엄)
    # 를 기본값으로, LLM 이 명시 제공하면 그 값으로 덮는다(_pick). 요인모형 강건화.
    fb = anc.get("factor_betas") or {}
    factors = [
        val.FactorExposure("시장", _pick(a.get("market_beta"), fb.get("market_beta")) or 0,
                           _pick(a.get("market_premium"), fb.get("market_premium")) or 0),
        val.FactorExposure("SMB(규모)", _pick(a.get("smb_beta"), fb.get("smb_beta")) or 0,
                           _pick(a.get("smb_premium"), fb.get("smb_premium")) or 0),
        val.FactorExposure("HML(가치)", _pick(a.get("hml_beta"), fb.get("hml_beta")) or 0,
                           _pick(a.get("hml_premium"), fb.get("hml_premium")) or 0),
    ]
    return val.fama_french_valuation(
        forward_eps=_pick(a.get("forward_eps"), anc.get("eps_ttm")),
        risk_free=_pick(a.get("risk_free"), fb.get("risk_free")),
        factors=factors, earnings_growth=_num(a.get("earnings_growth")),
        equity_value=anc.get("market_cap_eok"), net_debt=anc.get("net_debt_eok"),
        roe=anc.get("roe_pct"), moat=anc.get("moat"),
        current_price=anc.get("current_price"),
    )


def _t_apt(a: dict, anc: dict) -> val.ValuationResult:
    # LLM 이 factors 를 명시하면 그걸, 아니면 실데이터 거시요인(시장베타 대리 + 관례 프리미엄)을 쓴다.
    fb = anc.get("factor_betas") or {}
    if a.get("factors"):
        factors = [
            val.FactorExposure(str(f.get("name") or "요인"), _num(f.get("beta")) or 0, _num(f.get("premium")) or 0)
            for f in a["factors"] if _num(f.get("beta")) is not None
        ]
    else:
        mb = fb.get("market_beta") or 1.0
        factors = [
            val.FactorExposure(name, round(mb * w, 3), prem)
            for (name, prem), w in zip(betamod.APT_FACTOR_PREMIUMS.items(), (1.0, 0.5, 0.5), strict=True)
        ]
    return val.apt_valuation(
        forward_eps=_pick(a.get("forward_eps"), anc.get("eps_ttm")),
        risk_free=_pick(a.get("risk_free"), fb.get("risk_free")),
        factors=factors, earnings_growth=_num(a.get("earnings_growth")),
        equity_value=anc.get("market_cap_eok"), net_debt=anc.get("net_debt_eok"),
        roe=anc.get("roe_pct"), moat=anc.get("moat"),
        current_price=anc.get("current_price"),
    )


def _grade_moat(business: dict) -> str | None:
    """business 단계의 해자 서술(prose)을 강|중|약 등급으로. 키워드 기반(LLM 자유서술 → 정성 배수용).

    '네트워크·독점·규제·특허·전환비용·진입장벽' 등 강한 신호 → 강. '경쟁 심화·범용·낮은' → 약. 기본 중.
    """
    text = str(business.get("moat") or "")
    if not text:
        return None
    strong = ("네트워크", "독점", "규제", "특허", "전환비용", "진입장벽", "높은 점유", "과점", "브랜드", "락인")
    weak = ("경쟁 심화", "범용", "낮은 진입", "치열", "제한적", "약한", "쉽게 모방")
    if any(k in text for k in weak):
        return "약"
    if sum(k in text for k in strong) >= 2:
        return "강"
    return "중"


# 섹터명 → 방식 적합도용 유형. 금융(은행·보험)은 EV·DCF 제외, 시클리컬은 PER 저가중.
_FINANCIAL_SECTORS = ("은행", "증권", "보험")
_CYCLICAL_SECTORS = ("반도체", "반도체 소부장", "2차전지", "철강", "조선", "에너지화학", "자동차", "기계장비")


def _classify_for_fit(ctx: ToolContext, prior: dict, anchors: dict) -> dict:
    """종목 유형·배당·이익 신호를 모아 방식 적합도(method_fit) 인자로 변환(코드 결정, 재현 가능).

    유형 = 섹터(금융·시클리컬, 코드) 우선 → 없으면 thesis_type(LLM 성장/자산주) → 기타. 배당·적자는
    앵커·재무로 게이트(무배당 DDM 제외, 적자 PER·DCF 제외). LLM 판정을 섹터·지표로 교차 보정.
    """
    sector = sector_for(ctx)
    thesis_type = str((prior.get("thesis") or {}).get("thesis_type") or "")
    if sector in _FINANCIAL_SECTORS:
        stock_type = "financial"
    elif sector in _CYCLICAL_SECTORS:
        stock_type = "cyclical"
    elif "성장" in thesis_type:
        stock_type = "growth"
    elif "자산" in thesis_type or "역발상" in thesis_type:
        stock_type = "asset"
    else:
        stock_type = "other"
    # 시가배당률: 앵커 값 우선, 없으면 연간 DPS/현재가로 산출. DDM 게이트(저배당이면 제외)에 쓴다.
    div_yield = anchors.get("div_yield_pct")
    if div_yield is None:
        dps, px = anchors.get("dps_annual"), anchors.get("current_price")
        if dps and px and px > 0:
            div_yield = dps / px * 100
    eps = anchors.get("eps_ttm")
    is_loss = eps is not None and eps < 0
    return {
        "stock_type": stock_type, "sector": sector, "thesis_type": thesis_type,
        "div_yield_pct": div_yield,
        "fit": val.method_fit(stock_type, div_yield_pct=div_yield, is_loss=is_loss),
    }


# 방식 도구 레지스트리: name → (계산 함수, 파라미터 JSON 스키마 properties).
_METHOD_TOOLS = {
    "compute_per": (_t_per, {"forward_eps": "number", "target_per": "number", "rationale": "string"}),
    "compute_pbr": (_t_pbr, {"target_pbr": "number", "rationale": "string"}),
    "compute_ev_ebitda": (_t_ev_ebitda, {"forward_ebitda_eok": "number", "target_ev_ebitda": "number", "rationale": "string"}),
    "compute_dcf": (_t_dcf, {"fcf_base_eok": "number", "growth_rate": "number", "years": "number",
                             "terminal_growth": "number", "discount_rate": "number", "rationale": "string"}),
    "compute_ddm": (_t_ddm, {"dividend_growth": "number", "cost_of_equity": "number", "rationale": "string"}),
    "compute_asset": (_t_asset, {"asset_premium": "number", "rationale": "string"}),
    "compute_fama_french": (_t_fama_french, {"market_beta": "number", "smb_beta": "number", "hml_beta": "number",
                                             "risk_free": "number", "market_premium": "number", "smb_premium": "number",
                                             "hml_premium": "number", "earnings_growth": "number", "rationale": "string"}),
    "compute_apt": (_t_apt, {"factors": "array", "risk_free": "number", "earnings_growth": "number", "rationale": "string"}),
}

# 도구명 → 방식 식별자(결과 dict 의 method 필드와 일치).
_TOOL_METHOD = {
    "compute_per": "per", "compute_pbr": "pbr", "compute_ev_ebitda": "ev_ebitda",
    "compute_dcf": "dcf", "compute_ddm": "ddm", "compute_asset": "asset",
    "compute_fama_french": "fama_french", "compute_apt": "apt",
}


def _tool_schema(name: str, desc: str, props: dict) -> dict:
    """properties dict(name→type) → Ollama/OpenAI function 스키마."""
    schema_props = {}
    for k, t in props.items():
        if t == "array":
            schema_props[k] = {"type": "array", "items": {"type": "object"}}
        else:
            schema_props[k] = {"type": t}
    return {"type": "function", "function": {
        "name": name, "description": desc, "parameters": {"type": "object", "properties": schema_props}}}


_TOOL_DESCS = {
    "compute_per": "PER 목표가 = 예상EPS(연환산) × 목표PER. forward_eps 미지정 시 앵커 TTM EPS 사용.",
    "compute_pbr": "PBR 목표가 = 주당순자산(BPS) × 목표PBR. 자산주·금융주.",
    "compute_ev_ebitda": "EV/EBITDA 목표가. 예상EBITDA(억원)×목표배수 − 순차입 → 주식수로 나눔.",
    "compute_dcf": "2단계 DCF. 기준FCF(억원)·성장률·연수·영구성장률·할인율(WACC)로 지분가치→주당.",
    "compute_ddm": "고든 배당할인. DPS·배당성장률·자기자본비용. 무배당이면 부적합.",
    "compute_asset": "자산가치 = 주당순자산 × 재평가/청산배수(청산할인<1<재평가할증).",
    "compute_fama_french": "Fama-French 3요인. 베타(시장회귀·시총/PBR 프록시)·프리미엄은 실데이터로 "
                           "자동 주입되므로 earnings_growth(이익성장률)만 주면 된다. 목표PER=1/(r−g).",
    "compute_apt": "APT 다요인. 베타·프리미엄 실데이터 자동 주입 → earnings_growth 만 주면 됨. "
                   "특정 요인 커스텀 시에만 factors=[{name,beta,premium}] 제공.",
}


def _build_tools() -> list[dict]:
    """8개 compute 도구 + get_anchors + blend + finalize 의 function 스키마."""
    tools = [_tool_schema(n, _TOOL_DESCS[n], props) for n, (_fn, props) in _METHOD_TOOLS.items()]
    tools.append(_tool_schema("get_anchors", "현재 실데이터 앵커(EPS TTM·BPS·EBITDA·배당·주식수·순차입)를 조회.", {}))
    tools.append(_tool_schema("blend", "지금까지 계산한 방식들의 신뢰도 가중 최종 목표가·스프레드를 확인.", {}))
    tools.append(_tool_schema("finalize", "분석 종료. 최종 결론·진입성격 확정.",
                              {"entry_case": "string", "conclusion": "string"}))
    return tools


def _result_to_dict(r: val.ValuationResult) -> dict:
    return {
        "method": r.method, "label": r.label, "applicable": r.applicable,
        "target_price": r.target_price, "upside_pct": r.upside_pct,
        "confidence": r.confidence, "assumptions": r.assumptions,
        "process": r.process, "note": r.note,
    }


_MAX_TURNS = 24  # 8방식 + 재계산 여유. 도구호출 없는 최종답변이 나오거나 이 한도면 종료.

_SYSTEM = (
    "너는 한국 주식 밸류에이션 애널리스트다. 8개 방식(PER·PBR·EV/EBITDA·DCF·DDM·자산가치·Fama-French·"
    "APT)으로 목표가를 구한다. 계산은 도구가 하므로 너는 각 방식의 *가정*을 근거와 함께 정해 도구를 호출하고, "
    "반환된 목표가·업사이드·경고(note)를 **직접 확인**한다.\n\n"
    "진행 절차:\n"
    "1) get_anchors 로 실데이터(EPS·BPS·EBITDA·배당·주식수·순차입)를 먼저 확인한다. eps_ttm·ebitda 앵커는 "
    "forward_meta 가 있으면 이미 예상(forward)치로 대체된 값이다(source: hitl|consensus|extrapolation, "
    "성장률·근거 포함). forward_meta 를 확인해 어떤 근거의 예상 이익인지 파악하고, 추가로 예상치를 손보려면 "
    "그 이유를 rationale 에 남긴다.\n"
    "2) 각 compute_* 도구를 호출해 방식별 목표가를 구한다. 무배당이면 compute_ddm 을 건너뛴다.\n"
    "3) 도구가 applicable=false·경고(note)를 주면(예: 할인율≤영구성장률, 적자로 PER 불가) 가정을 고쳐 "
    "재호출한다. 방식 간 목표가가 크게 어긋나면(예: 한 방식만 3배) 그 가정을 재검토한다.\n"
    "4) blend 로 최종 목표가·스프레드를 확인한다.\n"
    "5) finalize 로 진입성격(자산주/역발상|성장주)과 결론(어느 방식을 왜 더 신뢰하는지·업사이드 성격)을 낸다.\n\n"
    "가정은 반드시 앵커·피어·업종 특성에 근거한다. 예상 EPS 는 연환산(TTM) 기준이며 목표 멀티플도 연간 기준이다. "
    "목표 PER 은 앵커의 per_band(과거 10년 PER 밴드: 중앙값·p25·p75)를 기준선으로 삼아 정하되, 밴드 밖으로 "
    "잡을 땐 리레이팅/디레이팅 근거를 rationale 에 명시한다(도구가 밴드 이탈 시 경고를 남긴다). "
    "추측·과장 금지. 레드플래그(이익의 질 문제)가 있으면 멀티플을 보수적으로 잡는다."
)


def _hitl_context(hitl: dict | None) -> str:
    """HITL 검증 결과(claims)를 밸류에이션 프롬프트 블록으로. 없으면 빈 문자열.

    반박(probability 0)은 반영하지 않도록, 반영(1)은 100%, 가능성(0<p<1)은 그 비율만큼만 가정을
    조정하도록 확률과 반영지시를 함께 노출한다. 밸류에이션 LLM 이 확률 가중으로 가정에 녹인다."""
    if not hitl or not isinstance(hitl, dict):
        return ""
    claims = [c for c in (hitl.get("claims") or []) if isinstance(c, dict)]
    if not claims:
        return ""
    lines = [
        "\n[사용자 인풋 검증(HITL)] — 아래는 사용자 인풋을 추가 리서치로 검증한 결과다. "
        "verdict·probability 에 따라 밸류에이션 가정을 조정하라: 반박(prob 0)은 반영하지 말 것, "
        "반영(prob 1)은 valuation_impact 를 100% 반영, 출처확인(IR 등 신뢰할 1차 출처, prob 0.7~0.9)은 "
        "그 비율만큼 반영(공개 검증 안 됐다고 무시하지 말 것 — 신뢰할 출처다), 가능성(0<prob<0.6)은 "
        "probability 비율만큼만 반영(예: prob 0.4·'성장률 +5%p' → +2%p). 근거 없는 낙관·비관 금지. "
        "**수치형(numeric) claim 은 baseline(현재 기준치)+new_value 로 총량을 잡고, 전체 매출 비중"
        "(segment_revenue_share)을 곱해 전사 영향으로 환산해 반영하라(예: 용량 X→X+Y, 비중 W% → 전사 매출 기여).**"
    ]
    if hitl.get("_procedure_incomplete"):
        lines.append(
            "  ⚠️ 절차 미완료 표시(_procedure_incomplete): 일부 claim 의 기준치·환산 절차가 미완이니 "
            "해당 numeric 반영은 보수적으로(확률 하향) 취급하라."
        )
    for c in claims:
        base = (
            f"- [{c.get('verdict')}·확률 {c.get('probability')}] {c.get('claim')} "
            f"→ 조정: {c.get('valuation_impact')} (근거: {str(c.get('evidence') or '')[:180]})"
        )
        num = c.get("numeric") if isinstance(c.get("numeric"), dict) else None
        if c.get("claim_type") == "numeric" and num:
            base += (
                f"\n    [수치] 현재 {num.get('baseline')} + 신규 {num.get('new_value')}{num.get('unit') or ''}"
                f"(증분 {num.get('delta_pct')}%), 매출비중 {num.get('segment_revenue_share')}% "
                f"— 환산: {str(num.get('conversion_chain') or '')[:200]}"
            )
        lines.append(base)
    return "\n".join(lines)


def run_valuation(llm: LLMPort, model: str, ctx: ToolContext, prior: dict, series: list[dict]) -> dict:
    """에이전틱 밸류에이션 루프. chat_tools 로 compute 도구를 반복 호출·검증 → 최종 목표가 dict.

    반환 dict 가 valuation_json 으로 저장된다(프론트 ValuationCard 가 methods 배열을 렌더).
    tool-calling 미지원(구 LLM)·실패 시 원샷 폴백으로 최소 결과를 보장한다."""
    price = dispatch("price_context", ctx, {})
    anchors = collect_anchors(series, price)
    # HITL 이익 증분을 forward 이익 앵커에 결정론적 반영(프롬프트 경로만으론 미반영되던 긍정 인풋을 계산에 직결).
    anchors = apply_hitl_to_anchors(anchors, prior.get("hitl"))
    # 이익 앵커를 forward(예상)로 대체 — 소스 우선순위 HITL(위)>컨센서스>성장률 외삽. 사용 소스는 forward_meta 고지.
    anchors = apply_forward_earnings(anchors, series)
    # 실데이터 요인 베타(지수 일봉 회귀 + 시총/PBR 프록시) — Fama-French·APT 가 LLM 추정 대신 사용.
    anchors["factor_betas"] = compute_factor_betas(ctx, anchors, price.get("market"))
    # 해자 등급(business 단계 서술 → 강|중|약) — H-Model 감쇠기간 정성 배수(ROE 초과수익과 앙상블).
    anchors["moat"] = _grade_moat(prior.get("business", {}) or {})
    # 종목 유형별 방식 적합도(가중/제외) — blend 가 부적합 방식(금융주 EV/EBITDA·무배당 DDM 등)을 제외.
    cls = _classify_for_fit(ctx, prior, anchors)
    fit = cls["fit"]
    is_growth = cls["stock_type"] == "growth"  # blend 상방 이상치 컷 완화 여부
    # 시클리컬: 현재 TTM 이익이 사이클 고/저점이라 기준연도로 부적합 → 중간사이클 정규화 EPS 로 앵커 대체.
    if cls["stock_type"] == "cyclical":
        norm_eps, norm_meta = _normalized_eps(_sorted_actuals(series), anchors.get("eps_ttm"))
        if norm_eps is not None:
            anchors["eps_ttm"] = round(norm_eps, 1)
            anchors["eps_normalized"] = norm_meta  # 프론트·서술용(정규화 근거)
    peers = dispatch("peers", ctx, {})
    tools = _build_tools()

    results: dict[str, val.ValuationResult] = {}  # method → 최신 결과(재계산 시 덮어씀)
    final_meta: dict = {}

    context = (
        f"[종목] {ctx.code}\n"
        f"[투자 아이디어]\n{json.dumps(prior.get('thesis', {}), ensure_ascii=False)[:1500]}\n"
        f"[레드플래그]\n{json.dumps(prior.get('redflags', {}), ensure_ascii=False)[:1000]}\n"
        f"[실데이터 앵커]\n{json.dumps(anchors, ensure_ascii=False)}\n"
        f"[피어 밸류에이션]\n{json.dumps(peers, ensure_ascii=False)[:1500]}\n"
        f"[재무 시계열(최근)]\n{json.dumps(series[-6:], ensure_ascii=False)[:2000]}"
        + (
            f"\n[시클리컬 정규화] 이 종목은 경기순환주로 판정돼 eps_ttm 앵커를 현재 TTM 이 아니라 "
            f"**중간사이클 정규화 EPS** 로 대체했다({json.dumps(anchors.get('eps_normalized'), ensure_ascii=False)}). "
            "현재가 사이클 고/저점이라 현재 이익을 기준연도로 쓰면 과대/과소평가된다. PER·EV/EBITDA 목표배수는 "
            "이 정규화 이익에 맞춰 정한다(peak 이익에 낮은 배수를 곱하는 오류 금지)."
            if anchors.get("eps_normalized") else ""
        )
        + _hitl_context(prior.get("hitl"))
    )
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": context + "\n\nget_anchors 로 시작해 8개 방식을 계산하고 finalize 로 마쳐라."},
    ]

    def _run_tool(name: str, args: dict) -> dict:
        """도구 실행 → LLM 에 돌려줄 결과 dict. compute_* 는 domain 계산, 나머지는 앵커/blend/finalize."""
        if name in _METHOD_TOOLS:
            fn, _props = _METHOD_TOOLS[name]
            r = fn(args, anchors)
            if isinstance(args.get("rationale"), str) and args["rationale"]:
                r.note = (str(args["rationale"]) + (" " + r.note if r.note else "")).strip()
            results[r.method] = r
            return {"method": r.method, "applicable": r.applicable, "target_price": r.target_price,
                    "upside_pct": r.upside_pct, "note": r.note, "process": r.process}
        if name == "get_anchors":
            return anchors
        if name == "blend":
            summary = val.blend(list(results.values()), anchors.get("current_price"), fit, is_growth=is_growth)
            return {"final_target_price": summary.final_target, "final_upside_pct": summary.final_upside_pct,
                    "method_count": summary.method_count,
                    "targets": {m: r.target_price for m, r in results.items() if r.applicable}}
        if name == "finalize":
            final_meta["entry_case"] = args.get("entry_case")
            final_meta["conclusion"] = args.get("conclusion")
            final_meta["done"] = True
            return {"ok": True}
        return {"error": f"unknown tool: {name}"}

    try:
        for _turn in range(_MAX_TURNS):
            turn = llm.chat_tools(model, messages, tools)
            if not turn.tool_calls:
                # 도구호출 없는 응답 = 최종 서술. content 를 결론으로 회수하고 종료.
                if turn.content and not final_meta.get("conclusion"):
                    final_meta["conclusion"] = turn.content
                break
            messages.append(turn.raw_message or {"role": "assistant", "content": turn.content,
                                                 "tool_calls": [{"function": {"name": tc.name, "arguments": tc.arguments}} for tc in turn.tool_calls]})
            for tc in turn.tool_calls:
                out = _run_tool(tc.name, tc.arguments or {})
                messages.append({"role": "tool", "tool_name": tc.name,
                                 "content": json.dumps(out, ensure_ascii=False)[:2000]})
            if final_meta.get("done"):
                break
    except LLMError as e:
        logger.warning("valuation tool-loop failed %s: %s — 원샷 폴백", ctx.code, e)
        return _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series, is_growth=is_growth)

    if not results:  # 도구를 한 번도 못 돌렸으면(모델이 곧장 서술) 원샷 폴백.
        return _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series, fit, is_growth=is_growth)

    summary = val.blend(list(results.values()), anchors.get("current_price"), fit, is_growth=is_growth)
    ordered = [results[m] for _tool, m in _TOOL_METHOD.items() if m in results]
    return {
        "final_target_price": summary.final_target,
        "final_upside_pct": summary.final_upside_pct,
        "current_price": summary.current_price,
        "method_count": summary.method_count,
        "stock_type": cls["stock_type"],  # 분류(프론트·디버깅)
        "method_fit": fit,  # 방식별 적합도(0=제외) — 프론트가 제외 방식 표시 가능
        "forward_meta": anchors.get("forward_meta"),  # 예상 이익 소스·성장률 고지(프론트·서술)
        "entry_case": final_meta.get("entry_case"),
        "conclusion": final_meta.get("conclusion"),
        "methods": [_result_to_dict(r) for r in ordered],
    }


# ── 원샷 폴백(tool-calling 미지원·실패 시) ───────────────────────────────
_FALLBACK_SCHEMA = """{"per":{"target_per":수,"rationale":""},"pbr":{"target_pbr":수,"rationale":""},
"ev_ebitda":{"forward_ebitda_eok":수,"target_ev_ebitda":수,"rationale":""},
"dcf":{"fcf_base_eok":수,"growth_rate":수,"years":수,"terminal_growth":수,"discount_rate":수,"rationale":""},
"ddm":{"dividend_growth":수,"cost_of_equity":수,"rationale":""},"asset":{"asset_premium":수,"rationale":""},
"fama_french":{"market_beta":수,"smb_beta":수,"hml_beta":수,"risk_free":수,"market_premium":수,"smb_premium":수,"hml_premium":수,"earnings_growth":수,"rationale":""},
"apt":{"factors":[{"name":"","beta":수,"premium":수}],"risk_free":수,"earnings_growth":수,"rationale":""},
"forward_eps":수,"entry_case":"자산주/역발상|성장주","conclusion":""}"""


def _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series, fit=None, *, is_growth=False) -> dict:
    """구 방식(원샷 가정 blob) 폴백. tool-calling 이 안 되거나 루프가 결과를 못 낼 때 최소 결과 보장."""
    from app.services.sentiment import _extract_json
    user = (
        f"[종목] {ctx.code}\n[앵커]\n{json.dumps(anchors, ensure_ascii=False)}\n"
        f"[피어]\n{json.dumps(peers, ensure_ascii=False)[:1500]}"
        + _hitl_context(prior.get("hitl"))
        + f"\n8개 방식 가정 JSON 만 출력:\n{_FALLBACK_SCHEMA}"
    )
    try:
        a = _extract_json(llm.chat(model, "밸류에이션 가정만 JSON 으로 출력.", user, temperature=0.2)) or {}
    except LLMError:
        a = {}
    results = []
    for tool, method in _TOOL_METHOD.items():
        fn, _props = _METHOD_TOOLS[tool]
        method_args = dict(a.get(method) or {})
        if "forward_eps" in a and "forward_eps" not in method_args:
            method_args["forward_eps"] = a["forward_eps"]
        r = fn(method_args, anchors)
        if method_args.get("rationale"):
            r.note = (str(method_args["rationale"]) + (" " + r.note if r.note else "")).strip()
        results.append(r)
    summary = val.blend(results, anchors.get("current_price"), fit, is_growth=is_growth)
    return {
        "final_target_price": summary.final_target, "final_upside_pct": summary.final_upside_pct,
        "current_price": summary.current_price, "method_count": summary.method_count,
        "method_fit": fit, "forward_meta": anchors.get("forward_meta"),
        "entry_case": a.get("entry_case"), "conclusion": a.get("conclusion"),
        "methods": [_result_to_dict(r) for r in results],
    }
