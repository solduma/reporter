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

from app.domain import valuation as val
from app.ports.llm import LLMError, LLMPort
from app.services.deepdive.tools import ToolContext, dispatch

logger = logging.getLogger(__name__)


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
    """EBITDA 를 억원으로 정규화. financials.ebitda 컬럼은 구 valuation_ingest(원)·신
    report_ingest(억원) 데이터가 혼재한다 → 같은 기간 revenue(억원 확정)와의 비율로 단위를 추정해 보정.

    EBITDA 마진(ebitda/revenue)은 정상적으로 |비율|<10 이다. 비율이 1e4 이상이면 ebitda 가 원 단위
    (revenue 는 억원)라는 뜻 → /1e8. revenue 가 없으면 절대크기로 추정(억원 종목 매출이 조 단위를
    넘는 경우는 드묾: 1e7억=1000조 초과면 원 단위로 간주)."""
    if revenue and revenue > 0:
        return ebitda / 1e8 if abs(ebitda / revenue) > 1e4 else ebitda
    return ebitda / 1e8 if abs(ebitda) > 1e7 else ebitda


def _latest_annual_ebitda_eok(rows: list[dict]) -> float | None:
    """연간(.12) 최신 EBITDA 를 억원으로 정규화해 반환(단위 혼재 방어)."""
    for r in reversed(rows):
        if _period_key(r["period"])[1] == 12:  # type: ignore[index]
            v = _num(r.get("ebitda"))
            if v is not None:
                return _ebitda_to_eok(v, _num(r.get("revenue")))
    return None


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
    ebitda = _latest_annual_ebitda_eok(rows)  # 억원으로 정규화(원·억원 혼재 방어)
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
        "current_pbr": _latest_pointintime(rows, "pbr"),
        "current_ev_ebitda": ev_ebitda,
        "div_yield_pct": _latest_pointintime(rows, "div_yield"),
        "shares": shares, "net_debt_eok": net_debt,
    }


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
    )


def _t_pbr(a: dict, anc: dict) -> val.ValuationResult:
    return val.pbr_valuation(
        bps=_pick(a.get("bps"), anc.get("bps")),
        target_pbr=_num(a.get("target_pbr")), current_price=anc.get("current_price"),
    )


def _t_ev_ebitda(a: dict, anc: dict) -> val.ValuationResult:
    # 순차입은 앵커(EV/EBITDA 역산, 억원 정규화)만 사용 — LLM 이 원문에서 잘못된 단위로 넘길 위험 차단.
    return val.ev_ebitda_valuation(
        forward_ebitda=_pick(a.get("forward_ebitda_eok"), anc.get("ebitda_eok_annual")),
        target_ev_ebitda=_num(a.get("target_ev_ebitda")),
        net_debt=anc.get("net_debt_eok"),
        shares=anc.get("shares"), current_price=anc.get("current_price"),
    )


def _t_dcf(a: dict, anc: dict) -> val.ValuationResult:
    return val.dcf_valuation(
        fcf_base=_num(a.get("fcf_base_eok")), growth_rate=_num(a.get("growth_rate")),
        years=int(_num(a.get("years")) or 5), terminal_growth=_num(a.get("terminal_growth")),
        discount_rate=_num(a.get("discount_rate")),
        net_debt=anc.get("net_debt_eok"),  # 앵커만(억원 정규화). LLM 단위 오류 차단.
        shares=anc.get("shares"), current_price=anc.get("current_price"),
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
    factors = [
        val.FactorExposure("시장", _num(a.get("market_beta")) or 0, _num(a.get("market_premium")) or 0),
        val.FactorExposure("SMB(규모)", _num(a.get("smb_beta")) or 0, _num(a.get("smb_premium")) or 0),
        val.FactorExposure("HML(가치)", _num(a.get("hml_beta")) or 0, _num(a.get("hml_premium")) or 0),
    ] if a.get("market_beta") is not None else []
    return val.fama_french_valuation(
        forward_eps=_pick(a.get("forward_eps"), anc.get("eps_ttm")), risk_free=_num(a.get("risk_free")),
        factors=factors, earnings_growth=_num(a.get("earnings_growth")),
        current_price=anc.get("current_price"),
    )


def _t_apt(a: dict, anc: dict) -> val.ValuationResult:
    factors = [
        val.FactorExposure(str(f.get("name") or "요인"), _num(f.get("beta")) or 0, _num(f.get("premium")) or 0)
        for f in (a.get("factors") or []) if _num(f.get("beta")) is not None
    ]
    return val.apt_valuation(
        forward_eps=_pick(a.get("forward_eps"), anc.get("eps_ttm")), risk_free=_num(a.get("risk_free")),
        factors=factors, earnings_growth=_num(a.get("earnings_growth")),
        current_price=anc.get("current_price"),
    )


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
    "compute_fama_french": "Fama-French 3요인 요구수익률 → 목표PER=1/(r−g) → 목표가.",
    "compute_apt": "APT 다요인(factors=[{name,beta,premium}]) 요구수익률 → 목표PER → 목표가.",
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
    "1) get_anchors 로 실데이터(EPS TTM·BPS·EBITDA·배당·주식수·순차입)를 먼저 확인한다.\n"
    "2) 각 compute_* 도구를 호출해 방식별 목표가를 구한다. 무배당이면 compute_ddm 을 건너뛴다.\n"
    "3) 도구가 applicable=false·경고(note)를 주면(예: 할인율≤영구성장률, 적자로 PER 불가) 가정을 고쳐 "
    "재호출한다. 방식 간 목표가가 크게 어긋나면(예: 한 방식만 3배) 그 가정을 재검토한다.\n"
    "4) blend 로 최종 목표가·스프레드를 확인한다.\n"
    "5) finalize 로 진입성격(자산주/역발상|성장주)과 결론(어느 방식을 왜 더 신뢰하는지·업사이드 성격)을 낸다.\n\n"
    "가정은 반드시 앵커·피어·업종 특성에 근거한다. 예상 EPS 는 연환산(TTM) 기준이며 목표 멀티플도 연간 기준이다. "
    "추측·과장 금지. 레드플래그(이익의 질 문제)가 있으면 멀티플을 보수적으로 잡는다."
)


def run_valuation(llm: LLMPort, model: str, ctx: ToolContext, prior: dict, series: list[dict]) -> dict:
    """에이전틱 밸류에이션 루프. chat_tools 로 compute 도구를 반복 호출·검증 → 최종 목표가 dict.

    반환 dict 가 valuation_json 으로 저장된다(프론트 ValuationCard 가 methods 배열을 렌더).
    tool-calling 미지원(구 LLM)·실패 시 원샷 폴백으로 최소 결과를 보장한다."""
    price = dispatch("price_context", ctx, {})
    anchors = collect_anchors(series, price)
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
            summary = val.blend(list(results.values()), anchors.get("current_price"))
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
        return _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series)

    if not results:  # 도구를 한 번도 못 돌렸으면(모델이 곧장 서술) 원샷 폴백.
        return _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series)

    summary = val.blend(list(results.values()), anchors.get("current_price"))
    ordered = [results[m] for _tool, m in _TOOL_METHOD.items() if m in results]
    return {
        "final_target_price": summary.final_target,
        "final_upside_pct": summary.final_upside_pct,
        "current_price": summary.current_price,
        "method_count": summary.method_count,
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


def _oneshot_fallback(llm, model, ctx, prior, anchors, peers, series) -> dict:
    """구 방식(원샷 가정 blob) 폴백. tool-calling 이 안 되거나 루프가 결과를 못 낼 때 최소 결과 보장."""
    from app.services.sentiment import _extract_json
    user = (
        f"[종목] {ctx.code}\n[앵커]\n{json.dumps(anchors, ensure_ascii=False)}\n"
        f"[피어]\n{json.dumps(peers, ensure_ascii=False)[:1500]}\n"
        f"8개 방식 가정 JSON 만 출력:\n{_FALLBACK_SCHEMA}"
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
    summary = val.blend(results, anchors.get("current_price"))
    return {
        "final_target_price": summary.final_target, "final_upside_pct": summary.final_upside_pct,
        "current_price": summary.current_price, "method_count": summary.method_count,
        "entry_case": a.get("entry_case"), "conclusion": a.get("conclusion"),
        "methods": [_result_to_dict(r) for r in results],
    }
