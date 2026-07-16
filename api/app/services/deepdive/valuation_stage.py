"""딥다이브 5단계 Valuation — 8개 밸류에이션 방식을 종합해 최종 목표가를 낸다.

역할 분리(hexagonal):
- **판단(가정)**은 LLM: 예상 EPS·목표 멀티플·성장률·할인율·베타·요인 프리미엄 등을 근거와 함께 제시.
- **산식·목표가·과정 서술**은 domain.valuation(순수·재현 가능)이 소유 — 환각 없는 결정론적 계산.

흐름: (1) 실데이터 앵커(eps·bps·ebitda·dps·주식수·순차입) 수집 → (2) LLM 이 방식별 가정 JSON 산출
→ (3) domain 이 각 방식 목표가 계산 → (4) 신뢰도 가중 blend → (5) 최종 목표가/방식별 결과 반환.
LLM 결과 형태가 딥다이브 저장 스키마(valuation_json)의 새 구조가 된다(프론트가 이걸 렌더).
"""

from __future__ import annotations

import json
import logging

from app.domain import valuation as val
from app.ports.llm import LLMError, LLMPort
from app.services.deepdive.tools import ToolContext, dispatch
from app.services.sentiment import _extract_json

logger = logging.getLogger(__name__)


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


def _latest_actual(series: list[dict], field: str) -> float | None:
    """실적(비추정) 시계열에서 field 의 최신 유효값."""
    for row in reversed(series):
        v = _num(row.get(field))
        if v is not None:
            return v
    return None


def collect_anchors(ctx: ToolContext, series: list[dict], price: dict) -> dict:
    """밸류에이션 실데이터 앵커. 주식수·순차입은 시총·EV/EBITDA 로 역산(별도 조회 없이).

    - eps/bps/ebitda/dps: 재무 시계열 최신 실적값.
    - shares = 시총 / 현재가(원). net_debt = EV − 시총 = ebitda×ev_ebitda − 시총(억원).
    """
    current_price = _num(price.get("close_price"))
    market_cap = _num(price.get("market_cap"))  # 원
    eps = _latest_actual(series, "eps")
    bps = _latest_actual(series, "bps")
    ebitda = _latest_actual(series, "ebitda")  # 억원
    dps = _latest_actual(series, "dps")
    ev_ebitda = _latest_actual(series, "ev_ebitda")

    shares = None
    if market_cap and current_price and current_price > 0:
        shares = market_cap / current_price
    net_debt = None  # 억원
    if ebitda and ev_ebitda and market_cap:
        net_debt = ebitda * ev_ebitda - market_cap / 1e8  # EV − 시총(억원)

    return {
        "current_price": current_price,
        "market_cap_eok": market_cap / 1e8 if market_cap else None,
        "eps": eps, "bps": bps, "ebitda_eok": ebitda, "dps": dps,
        "current_per": _latest_actual(series, "per"),
        "current_pbr": _latest_actual(series, "pbr"),
        "current_ev_ebitda": ev_ebitda,
        "div_yield_pct": _latest_actual(series, "div_yield"),
        "shares": shares, "net_debt_eok": net_debt,
    }


# LLM 이 채우는 방식별 가정 스키마(근거 필수). domain 이 이 숫자로 목표가를 계산한다.
_ASSUMPTION_SCHEMA = """{
  "forward_eps": 예상 주당순이익(원, 내년 추정),
  "per": {"target_per": 목표 PER 배수, "rationale": "멀티플 근거(피어·성장성)"},
  "pbr": {"target_pbr": 목표 PBR 배수, "rationale": "근거"},
  "ev_ebitda": {"forward_ebitda_eok": 예상 EBITDA(억원), "target_ev_ebitda": 목표 배수, "rationale": "근거"},
  "dcf": {"fcf_base_eok": 기준 FCF(억원), "growth_rate": 명시적성장률(예 0.10), "years": 명시적연수,
          "terminal_growth": 영구성장률(예 0.02), "discount_rate": WACC(예 0.09), "rationale": "근거"},
  "ddm": {"dividend_growth": 배당성장률, "cost_of_equity": 자기자본비용, "rationale": "근거(무배당이면 skip)"},
  "asset": {"asset_premium": 재평가/청산배수(청산할인<1<재평가할증), "rationale": "근거"},
  "fama_french": {"market_beta": 시장베타, "smb_beta": 규모베타, "hml_beta": 가치베타,
                  "risk_free": 무위험수익률(예 0.032), "market_premium": 시장프리미엄(예 0.06),
                  "smb_premium": 규모프리미엄(예 0.02), "hml_premium": 가치프리미엄(예 0.03),
                  "earnings_growth": 이익성장률, "rationale": "근거"},
  "apt": {"factors": [{"name": "요인명(금리/경기/환율 등)", "beta": 노출, "premium": 프리미엄}],
          "risk_free": 무위험수익률, "earnings_growth": 이익성장률, "rationale": "근거"},
  "entry_case": "자산주/역발상|성장주",
  "conclusion": "최종 결론 한 문단(어느 방식을 주로 신뢰하는지·업사이드 성격)"
}"""

_SYSTEM = (
    "너는 한국 주식 밸류에이션 애널리스트다. 8개 밸류에이션 방식(PER·PBR·EV/EBITDA·DCF·DDM·자산가치·"
    "Fama-French·APT)에 필요한 *가정*을 근거와 함께 제시한다. 실제 계산은 시스템이 하므로 너는 숫자 가정만 "
    "낸다. 제공된 실데이터 앵커(현재 EPS·BPS·EBITDA·배당·주식수·순차입)를 출발점으로, 업종·성장성·피어를 "
    "고려해 합리적으로 추정한다. 무배당이면 ddm 을 생략(빈 객체)하고, 적자면 이익 기반 방식의 forward 값을 "
    "정직하게(추정 근거와 함께) 낸다. 모든 rationale 은 데이터에 근거하고 과장하지 않는다.\n\n"
    "출력은 아래 JSON 하나만(다른 텍스트 금지):\n{schema}"
)


def _build_results(assumptions: dict, anchors: dict) -> list[val.ValuationResult]:
    """LLM 가정 + 실데이터 앵커 → domain 계산. 각 방식 독립(하나 실패해도 나머지 진행)."""
    cur = anchors.get("current_price")
    forward_eps = _num(assumptions.get("forward_eps")) or anchors.get("eps")
    results: list[val.ValuationResult] = []

    per_a = assumptions.get("per") or {}
    results.append(val.per_valuation(
        forward_eps=forward_eps, target_per=_num(per_a.get("target_per")), current_price=cur,
    ))

    pbr_a = assumptions.get("pbr") or {}
    results.append(val.pbr_valuation(
        bps=anchors.get("bps"), target_pbr=_num(pbr_a.get("target_pbr")), current_price=cur,
    ))

    ev_a = assumptions.get("ev_ebitda") or {}
    results.append(val.ev_ebitda_valuation(
        forward_ebitda=_num(ev_a.get("forward_ebitda_eok")) or anchors.get("ebitda_eok"),
        target_ev_ebitda=_num(ev_a.get("target_ev_ebitda")),
        net_debt=anchors.get("net_debt_eok"), shares=anchors.get("shares"), current_price=cur,
    ))

    dcf_a = assumptions.get("dcf") or {}
    results.append(val.dcf_valuation(
        fcf_base=_num(dcf_a.get("fcf_base_eok")), growth_rate=_num(dcf_a.get("growth_rate")),
        years=int(_num(dcf_a.get("years")) or 5), terminal_growth=_num(dcf_a.get("terminal_growth")),
        discount_rate=_num(dcf_a.get("discount_rate")), net_debt=anchors.get("net_debt_eok"),
        shares=anchors.get("shares"), current_price=cur,
    ))

    ddm_a = assumptions.get("ddm") or {}
    results.append(val.ddm_valuation(
        dps=anchors.get("dps"), dividend_growth=_num(ddm_a.get("dividend_growth")),
        cost_of_equity=_num(ddm_a.get("cost_of_equity")), current_price=cur,
    ))

    asset_a = assumptions.get("asset") or {}
    results.append(val.asset_valuation(
        book_equity_per_share=anchors.get("bps"),
        asset_premium=_num(asset_a.get("asset_premium")), current_price=cur,
    ))

    ff = assumptions.get("fama_french") or {}
    ff_factors = [
        val.FactorExposure("시장", _num(ff.get("market_beta")) or 0, _num(ff.get("market_premium")) or 0),
        val.FactorExposure("SMB(규모)", _num(ff.get("smb_beta")) or 0, _num(ff.get("smb_premium")) or 0),
        val.FactorExposure("HML(가치)", _num(ff.get("hml_beta")) or 0, _num(ff.get("hml_premium")) or 0),
    ] if ff.get("market_beta") is not None else []
    results.append(val.fama_french_valuation(
        forward_eps=forward_eps, risk_free=_num(ff.get("risk_free")), factors=ff_factors,
        earnings_growth=_num(ff.get("earnings_growth")), current_price=cur,
    ))

    apt_a = assumptions.get("apt") or {}
    apt_factors = [
        val.FactorExposure(str(f.get("name") or "요인"), _num(f.get("beta")) or 0, _num(f.get("premium")) or 0)
        for f in (apt_a.get("factors") or []) if _num(f.get("beta")) is not None
    ]
    results.append(val.apt_valuation(
        forward_eps=forward_eps, risk_free=_num(apt_a.get("risk_free")), factors=apt_factors,
        earnings_growth=_num(apt_a.get("earnings_growth")), current_price=cur,
    ))

    # LLM rationale 을 각 결과에 주입(방식별 근거 표시용).
    for r in results:
        a = assumptions.get(r.method) or {}
        if isinstance(a, dict) and a.get("rationale"):
            r.note = (str(a["rationale"]) + (" " + r.note if r.note else "")).strip()
    return results


def _result_to_dict(r: val.ValuationResult) -> dict:
    return {
        "method": r.method, "label": r.label, "applicable": r.applicable,
        "target_price": r.target_price, "upside_pct": r.upside_pct,
        "confidence": r.confidence, "assumptions": r.assumptions,
        "process": r.process, "note": r.note,
    }


def run_valuation(llm: LLMPort, model: str, ctx: ToolContext, prior: dict, series: list[dict]) -> dict:
    """5단계 실행: LLM 가정 → domain 계산 → blend. 반환 dict 가 valuation_json 으로 저장된다."""
    price = dispatch("price_context", ctx, {})
    anchors = collect_anchors(ctx, series, price)
    peers = dispatch("peers", ctx, {})

    user = (
        f"[종목] {ctx.code}\n"
        f"[투자 아이디어]\n{json.dumps(prior.get('thesis', {}), ensure_ascii=False)[:2000]}\n\n"
        f"[실데이터 앵커]\n{json.dumps(anchors, ensure_ascii=False)}\n\n"
        f"[피어 밸류에이션]\n{json.dumps(peers, ensure_ascii=False)[:2000]}\n\n"
        f"[재무 시계열(최근)]\n{json.dumps(series[-8:], ensure_ascii=False)[:3000]}\n\n"
        "위 데이터로 8개 방식의 가정 JSON 을 출력해라."
    )
    system = _SYSTEM.format(schema=_ASSUMPTION_SCHEMA)
    try:
        raw = llm.chat(model, system, user, temperature=0.2)
        assumptions = _extract_json(raw) or {}
    except LLMError as e:
        logger.warning("valuation LLM failed %s: %s", ctx.code, e)
        assumptions = {}

    results = _build_results(assumptions, anchors)
    summary = val.blend(results, anchors.get("current_price"))

    return {
        "final_target_price": summary.final_target,
        "final_upside_pct": summary.final_upside_pct,
        "current_price": summary.current_price,
        "method_count": summary.method_count,
        "entry_case": assumptions.get("entry_case"),
        "conclusion": assumptions.get("conclusion"),
        "methods": [_result_to_dict(r) for r in results],
    }
