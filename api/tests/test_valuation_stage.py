"""딥다이브 밸류에이션 스테이지 테스트 — 앵커(TTM·연간) + 에이전틱 tool-loop + 원샷 폴백."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

from app.ports.llm import LLMError, ToolCall, ToolTurn
from app.services.deepdive import valuation_stage as vs


# ── 앵커(P0 버그 회귀) ───────────────────────────────────────────────────
def _series():
    # 결정론 방식(PER 밴드·정당배수·FCFF)이 작동하도록 충분한 분기 시계열. 분기 EPS(개별값) +
    # 연간(.12) 재무. per/pbr 은 밴드(≥4 표본)용으로 여러 분기에, 연간엔 FCFF 재료(op·D&A·capex·세율).
    rows = []
    eps = 800
    for yr in (2022, 2023, 2024, 2025):
        for mo, per, pbr in (("03", 11, 1.1), ("06", 12, 1.2), ("09", 13, 1.15), ("12", 14, 1.3)):
            r = {"period": f"{yr}.{mo}", "is_estimate": False, "eps": eps,
                 "bps": 45000 + (yr - 2022) * 2000, "per": per, "pbr": pbr}
            if mo == "12":  # 연간 재무(EBITDA·배당·FCFF 재료)
                r.update(revenue=1600, operating_income=240, ebitda=800, dps=500,
                         ev_ebitda=6.0, div_yield=2.0, roe=9.0,
                         depreciation=180, capex=90, effective_tax_rate=0.16)
            rows.append(r)
            eps = round(eps * 1.05)  # 분기마다 ~5% 성장(실현 CAGR 산출용)
    return rows


def test_anchor_eps_is_ttm_not_single_quarter():
    # P0: 분기 EPS(1500) 가 아니라 최근 4분기 합(1200+1300+1500+... =5100)이어야 한다.
    s = _series()
    anc = vs.collect_anchors(s, {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["eps_ttm"] == sum(r["eps"] for r in s[-4:])  # 최근 4분기 합
    assert anc["eps_ttm"] != s[-1]["eps"]  # 단일분기 아님


def test_anchor_ebitda_and_dps_are_annual():
    s = _series()
    anc = vs.collect_anchors(s, {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["ebitda_eok_annual"] == 800  # .12 연간
    assert anc["dps_annual"] == 500
    assert anc["bps"] == s[-1]["bps"]  # 최신 시점값


def test_anchor_shares_and_net_debt_derived():
    anc = vs.collect_anchors(_series(), {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["shares"] == 400_000_000_000 / 40000  # 시총/현재가
    # net_debt = ebitda(800) × ev_ebitda(6) − 시총(4000억) = 4800 − 4000 = 800
    assert anc["net_debt_eok"] == 800


# ── HITL 이익 증분의 앵커 반영(문제2: 긍정 인풋이 계산에 결정론적으로 들어가게) ──────────
def _hitl(claims):
    return {"claims": claims}


def test_apply_hitl_uplifts_forward_earnings():
    # 결정론 HITL: 순이익 지표 %·전사(company) claim → 이익증분율 그대로 eps_ttm·ebitda 에 곱.
    # net_income·company 는 매출→이익 전이 불필요(이미 이익 지표) → +20%.
    anc = {"eps_ttm": 1000.0, "ebitda_eok_annual": 500.0}
    claim = {"claim_type": "numeric", "refuted": False,
             "numeric": {"value": 20, "unit": "pct", "target_metric": "net_income", "scope": "company"}}
    out = vs.apply_hitl_to_anchors(anc, _hitl([claim]))
    assert out["eps_ttm"] == 1200.0  # +20%
    assert out["ebitda_eok_annual"] == 600.0
    assert out["hitl_earnings_uplift"]["uplift_pct"] == 20.0


def test_apply_hitl_segment_scope_scales_by_share():
    # 세그먼트 이익 +50% × 전사 비중 40% = 전사 이익 +20%.
    anc = {"eps_ttm": 1000.0}
    claim = {"claim_type": "numeric", "refuted": False,
             "numeric": {"value": 50, "unit": "pct", "target_metric": "net_income",
                         "scope": "segment", "segment_revenue_share": 40}}
    out = vs.apply_hitl_to_anchors(anc, _hitl([claim]))
    assert out["eps_ttm"] == 1200.0


def test_apply_hitl_no_arbitrary_cap():
    # 임의 상한 제거 — 큰 증분도 그대로 반영(결정론 계산이라 폭주 없음). +100% → ×2.0.
    anc = {"eps_ttm": 1000.0}
    claim = {"claim_type": "numeric", "refuted": False,
             "numeric": {"value": 100, "unit": "pct", "target_metric": "net_income", "scope": "company"}}
    out = vs.apply_hitl_to_anchors(anc, _hitl([claim]))
    assert out["eps_ttm"] == 2000.0  # 캡 없음
    assert "capped" not in out["hitl_earnings_uplift"]


def test_apply_hitl_skips_when_components_missing():
    # value·unit 등 구성요소 없으면 코드가 매출증분율 못 구해 조정 안 함(프롬프트 경로 위임).
    anc = {"eps_ttm": 1000.0}
    claim = {"claim_type": "numeric", "refuted": False,
             "numeric": {"value": None, "unit": "pct", "target_metric": "net_income", "scope": "company"}}
    out = vs.apply_hitl_to_anchors(anc, _hitl([claim]))
    assert out["eps_ttm"] == 1000.0  # 불변
    assert "hitl_earnings_uplift" not in out


def test_apply_hitl_refuted_claim_no_uplift():
    # 반박(refuted=true)은 미반영.
    anc = {"eps_ttm": 1000.0}
    claim = {"claim_type": "numeric", "refuted": True,
             "numeric": {"value": 50, "unit": "pct", "target_metric": "net_income", "scope": "company"}}
    out = vs.apply_hitl_to_anchors(anc, _hitl([claim]))
    assert out["eps_ttm"] == 1000.0


def test_apply_hitl_no_hitl_noop():
    anc = {"eps_ttm": 1000.0}
    assert vs.apply_hitl_to_anchors(anc, None) == anc
    assert vs.apply_hitl_to_anchors(anc, {}) == anc


def test_anchor_ttm_none_when_under_4_quarters():
    short = [{"period": "2026.03", "is_estimate": False, "eps": 1500, "bps": 53000}]
    anc = vs.collect_anchors(short, {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["eps_ttm"] is None  # 4분기 미만 → TTM 신뢰불가


# ── 시클리컬 정규화 EPS(mid-cycle) ────────────────────────────────────────
def _cyclical_series(peak: bool):
    # 12개 분기(TTM 창 9개≥6): 마진이 5~20% 로 출렁이는 시클리컬. peak=True 면 최신이 고마진.
    lows = [0.05, 0.06, 0.07, 0.05, 0.06, 0.07]  # 저마진 국면
    highs = [0.15, 0.18, 0.20, 0.16, 0.19, 0.20]  # 고마진 국면
    order = lows + highs if peak else highs + lows
    rows = []
    for i, m in enumerate(order):
        rev = 1000.0
        rows.append({"period": f"20{23 + i // 4}.{(i % 4 + 1) * 3:02d}", "is_estimate": False,
                     "revenue": rev, "net_income": rev * m, "eps": rev * m / 10})
    return rows


def test_normalized_eps_lowers_at_peak():
    # 현재가 사이클 고점(고마진)이면 정규화 EPS < TTM(하향) — peak-PER 과대평가 방지.
    rows = vs._sorted_actuals(_cyclical_series(peak=True))
    ttm = vs._ttm_eps(rows)
    norm, meta = vs._normalized_eps(rows, ttm)
    assert norm is not None and norm < ttm
    assert meta["mid_cycle_margin"] < meta["current_margin"]


def test_normalized_eps_raises_at_trough():
    # 현재가 사이클 저점(저마진)이면 정규화 EPS > TTM(상향) — trough 과소평가 방지.
    rows = vs._sorted_actuals(_cyclical_series(peak=False))
    ttm = vs._ttm_eps(rows)
    norm, meta = vs._normalized_eps(rows, ttm)
    assert norm is not None and norm > ttm
    assert meta["mid_cycle_margin"] > meta["current_margin"]


def test_normalized_eps_none_when_insufficient_history():
    # 사이클 판단 히스토리(6개 TTM 창=9분기) 부족하면 None(TTM 그대로 사용).
    rows = vs._sorted_actuals(_cyclical_series(peak=True)[:5])
    norm, meta = vs._normalized_eps(rows, vs._ttm_eps(rows))
    assert norm is None and meta is None


# ── EBITDA 단위 읽기시점 2차 방어(DB 정규화가 근본, 이건 belt-and-suspenders) ──────────
def test_read_guard_ebitda_won_to_eok():
    # 원 단위(마진 1e8) → 억원 보정. 억원(정상 마진) → 그대로.
    assert vs._ebitda_to_eok(49_525_162_351, 412) == 49_525_162_351 / 1e8
    assert vs._ebitda_to_eok(495, 412) == 495
    assert vs._ebitda_to_eok(49_525_162_351, None) == 49_525_162_351 / 1e8  # revenue 결측 시 크기로


def test_anchor_ebitda_read_guard_normalizes():
    # 원단위 EBITDA 행이 남아있어도 앵커는 억원으로 정규화(구 데이터·배치 지연 방어).
    rows = [
        {"period": "2025.12", "is_estimate": False, "eps": 800, "bps": 42737,
         "ebitda": 49_525_162_351, "revenue": 412, "ev_ebitda": 13.93},
    ]
    anc = vs.collect_anchors(rows, {"close_price": 133400, "market_cap": 651_000_000_000})
    assert abs(anc["ebitda_eok_annual"] - 495.25) < 1


# ── 에이전틱 tool-loop ───────────────────────────────────────────────────
def _fake_llm(turns):
    llm = MagicMock()
    llm.chat_tools.side_effect = turns
    return llm


def _ctx():
    ctx = MagicMock()
    ctx.code = "000000"
    return ctx


@contextlib.contextmanager
def _patch_dispatch():
    # dispatch(가격·피어) + factor_betas(시장베타·rf·ERP 실측 배치값을 테스트용 고정) 목킹.
    # 실측 배치(ECOS·Damodaran) 없이도 요인·WACC 경로가 결정론 값을 쓰게 한다.
    fb = {"market_beta": 1.0, "risk_free": 0.032, "risk_free_10y": 0.035,
          "market_premium": 0.05, "beta_source": "테스트"}
    with (
        patch.object(vs, "dispatch",
                     lambda n, c, a: {"close_price": 40000, "market_cap": 400_000_000_000}
                     if n == "price_context" else {"peers": []}),
        patch.object(vs, "compute_factor_betas", lambda ctx, anc, mkt: fb),
        patch.object(vs.market_peg_ingest, "latest_market_peg", lambda db: 1.0),
    ):
        yield


def test_agentic_loop_computes_and_finalizes():
    # 결정론: compute_* 는 rationale 만 받고 목표가는 앵커로 코드가 확정. 호출한 방식만 계산됨.
    turns = [
        ToolTurn("", [ToolCall("1", "get_anchors", {})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("2", "compute_per", {"rationale": "성장 반영"})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("3", "compute_pbr", {"rationale": "수익성"})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("4", "finalize", {"entry_case": "성장주", "conclusion": "PER 중심"})], {"role": "assistant"}),
    ]
    with _patch_dispatch():
        out = vs.run_valuation(_fake_llm(turns), "m", _ctx(), {}, _series())
    assert out["method_count"] == 2  # per, pbr 만 호출
    assert out["entry_case"] == "성장주" and out["conclusion"] == "PER 중심"
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["applicable"] and per["target_price"] > 0  # 결정론 산출(구체값은 앵커 의존)
    assert out["final_target_price"] is not None


def test_agentic_loop_allows_recompute():
    # 같은 방식을 두 번 호출해도 결정론이라 결과 동일(덮어써도 값 불변) — 루프가 죽지 않음을 확인.
    turns = [
        ToolTurn("", [ToolCall("1", "compute_per", {"rationale": "초안"})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("2", "compute_per", {"rationale": "재검토"})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("3", "finalize", {"entry_case": "성장주", "conclusion": "수정함"})], {"role": "assistant"}),
    ]
    with _patch_dispatch():
        out = vs.run_valuation(_fake_llm(turns), "m", _ctx(), {}, _series())
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["applicable"] and per["target_price"] > 0


def test_agentic_loop_stops_on_no_tool_calls():
    # 도구 없이 서술만 오면 그 content 를 결론으로 회수하고 종료.
    turns = [
        ToolTurn("", [ToolCall("1", "compute_per", {"rationale": "x"})], {"role": "assistant"}),
        ToolTurn("PER 기준 목표가가 매력적이다.", [], {"role": "assistant"}),
    ]
    with _patch_dispatch():
        out = vs.run_valuation(_fake_llm(turns), "m", _ctx(), {}, _series())
    assert out["conclusion"] == "PER 기준 목표가가 매력적이다."
    assert out["method_count"] == 1


def test_falls_back_to_oneshot_when_tools_unsupported():
    # chat_tools 가 LLMError(구 LLM·미지원)면 원샷 폴백으로 최소 결과 보장.
    llm = MagicMock()
    llm.chat_tools.side_effect = LLMError("no tool support")
    llm.chat.return_value = json.dumps({
        "per": {"rationale": "성장"}, "entry_case": "성장주", "conclusion": "폴백 결론",
    })
    with _patch_dispatch():
        out = vs.run_valuation(llm, "m", _ctx(), {}, _series())
    assert out["conclusion"] == "폴백 결론"
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["applicable"] and per["target_price"] > 0  # 결정론 산출


def test_falls_back_when_no_tool_ever_called():
    # 모델이 곧장 서술만(도구 0회) → 결과 없음 → 원샷 폴백.
    llm = MagicMock()
    llm.chat_tools.return_value = ToolTurn("바로 결론", [], {"role": "assistant"})
    llm.chat.return_value = json.dumps({"pbr": {"rationale": "x"}, "conclusion": "폴백"})
    with _patch_dispatch():
        out = vs.run_valuation(llm, "m", _ctx(), {}, _series())
    assert out["method_count"] >= 1  # 폴백이 방식 계산
