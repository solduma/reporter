"""딥다이브 밸류에이션 스테이지 테스트 — 앵커(TTM·연간) + 에이전틱 tool-loop + 원샷 폴백."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.ports.llm import LLMError, ToolCall, ToolTurn
from app.services.deepdive import valuation_stage as vs


# ── 앵커(P0 버그 회귀) ───────────────────────────────────────────────────
def _series():
    # 분기 EPS(개별값) + 연간(.12) EBITDA·배당. 최신 행은 분기(2026.03).
    return [
        {"period": "2025.03", "is_estimate": False, "eps": 1000, "bps": 50000, "per": 10, "pbr": 1.0},
        {"period": "2025.06", "is_estimate": False, "eps": 1100, "bps": 50500},
        {"period": "2025.09", "is_estimate": False, "eps": 1200, "bps": 51000},
        {"period": "2025.12", "is_estimate": False, "eps": 1300, "bps": 52000,
         "ebitda": 800, "dps": 500, "ev_ebitda": 6.0, "div_yield": 2.0},
        {"period": "2026.03", "is_estimate": False, "eps": 1500, "bps": 53000},
    ]


def test_anchor_eps_is_ttm_not_single_quarter():
    # P0: 분기 EPS(1500) 가 아니라 최근 4분기 합(1200+1300+1500+... =5100)이어야 한다.
    anc = vs.collect_anchors(_series(), {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["eps_ttm"] == 1100 + 1200 + 1300 + 1500  # 최근 4분기
    assert anc["eps_ttm"] != 1500  # 단일분기 아님


def test_anchor_ebitda_and_dps_are_annual():
    anc = vs.collect_anchors(_series(), {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["ebitda_eok_annual"] == 800  # .12 연간
    assert anc["dps_annual"] == 500
    assert anc["bps"] == 53000  # 최신 시점값


def test_anchor_shares_and_net_debt_derived():
    anc = vs.collect_anchors(_series(), {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["shares"] == 400_000_000_000 / 40000  # 시총/현재가
    # net_debt = ebitda(800) × ev_ebitda(6) − 시총(4000억) = 4800 − 4000 = 800
    assert anc["net_debt_eok"] == 800


def test_anchor_ttm_none_when_under_4_quarters():
    short = [{"period": "2026.03", "is_estimate": False, "eps": 1500, "bps": 53000}]
    anc = vs.collect_anchors(short, {"close_price": 40000, "market_cap": 400_000_000_000})
    assert anc["eps_ttm"] is None  # 4분기 미만 → TTM 신뢰불가


def test_pick_respects_zero_and_negative_forward():
    # forward 가 0/음수여도(적자 예상) 앵커로 덮지 않는다.
    assert vs._pick(0, 5000) == 0.0
    assert vs._pick(-100, 5000) == -100.0
    assert vs._pick(None, 5000) == 5000  # None 만 폴백


# ── EBITDA 단위 정규화(원·억원 혼재 방어) ────────────────────────────────
def test_ebitda_unit_normalized_from_won():
    # 구 valuation_ingest 는 EBITDA 를 원으로 저장(revenue 는 억원). 비율 1e8 → /1e8 보정.
    # KINX 실제: ebitda 49,525,162,351원, revenue 412억 → 495억으로 정규화.
    assert vs._ebitda_to_eok(49_525_162_351, 412) == 49_525_162_351 / 1e8


def test_ebitda_already_eok_untouched():
    # 신 report_ingest 는 억원으로 저장. 정상 마진(495/412≈1.2) → 그대로.
    assert vs._ebitda_to_eok(495, 412) == 495


def test_ebitda_unit_by_magnitude_when_no_revenue():
    # revenue 결측이면 절대크기로 추정: 1e7억 초과면 원 단위로 간주.
    assert vs._ebitda_to_eok(49_525_162_351, None) == 49_525_162_351 / 1e8
    assert vs._ebitda_to_eok(495, None) == 495


def test_anchor_ebitda_normalized_and_net_debt_sane():
    # 원단위 EBITDA 행이어도 앵커는 억원으로 정규화 → net_debt 역산이 정상 범위.
    rows = [
        {"period": "2025.03", "is_estimate": False, "eps": 500, "bps": 40000},
        {"period": "2025.06", "is_estimate": False, "eps": 600, "bps": 41000},
        {"period": "2025.09", "is_estimate": False, "eps": 700, "bps": 42000},
        {"period": "2025.12", "is_estimate": False, "eps": 800, "bps": 42737,
         "ebitda": 49_525_162_351, "revenue": 412, "ev_ebitda": 13.93, "dps": 600},
    ]
    anc = vs.collect_anchors(rows, {"close_price": 133400, "market_cap": 651_000_000_000})
    assert abs(anc["ebitda_eok_annual"] - 495.25) < 1  # 억원 정규화
    # net_debt = 495 × 13.93 − 6510(시총억) ≈ 389억 (음수·조단위 아님)
    assert -1000 < anc["net_debt_eok"] < 2000


# ── 에이전틱 tool-loop ───────────────────────────────────────────────────
def _fake_llm(turns):
    llm = MagicMock()
    llm.chat_tools.side_effect = turns
    return llm


def _ctx():
    ctx = MagicMock()
    ctx.code = "000000"
    return ctx


def _patch_dispatch():
    return patch.object(
        vs, "dispatch",
        lambda n, c, a: {"close_price": 40000, "market_cap": 400_000_000_000}
        if n == "price_context" else {"peers": []},
    )


def test_agentic_loop_computes_and_finalizes():
    turns = [
        ToolTurn("", [ToolCall("1", "get_anchors", {})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("2", "compute_per", {"forward_eps": 4800, "target_per": 12})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("3", "compute_pbr", {"target_pbr": 1.2})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("4", "finalize", {"entry_case": "성장주", "conclusion": "PER 중심"})], {"role": "assistant"}),
    ]
    with _patch_dispatch():
        out = vs.run_valuation(_fake_llm(turns), "m", _ctx(), {}, _series())
    assert out["method_count"] == 2  # per, pbr 만 계산됨(나머지 미호출)
    assert out["entry_case"] == "성장주" and out["conclusion"] == "PER 중심"
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["applicable"] and per["target_price"] == 4800 * 12
    assert out["final_target_price"] is not None


def test_agentic_loop_allows_recompute():
    # 같은 방식을 두 번 호출하면 최신 결과로 덮어쓴다(자기수정).
    turns = [
        ToolTurn("", [ToolCall("1", "compute_per", {"forward_eps": 4800, "target_per": 30})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("2", "compute_per", {"forward_eps": 4800, "target_per": 12})], {"role": "assistant"}),
        ToolTurn("", [ToolCall("3", "finalize", {"entry_case": "성장주", "conclusion": "수정함"})], {"role": "assistant"}),
    ]
    with _patch_dispatch():
        out = vs.run_valuation(_fake_llm(turns), "m", _ctx(), {}, _series())
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["target_price"] == 4800 * 12  # 재계산된 값(30배 아님)


def test_agentic_loop_stops_on_no_tool_calls():
    # 도구 없이 서술만 오면 그 content 를 결론으로 회수하고 종료.
    turns = [
        ToolTurn("", [ToolCall("1", "compute_per", {"forward_eps": 4800, "target_per": 12})], {"role": "assistant"}),
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
        "per": {"target_per": 12}, "forward_eps": 4800,
        "entry_case": "성장주", "conclusion": "폴백 결론",
    })
    with _patch_dispatch():
        out = vs.run_valuation(llm, "m", _ctx(), {}, _series())
    assert out["conclusion"] == "폴백 결론"
    per = next(m for m in out["methods"] if m["method"] == "per")
    assert per["applicable"] and per["target_price"] == 4800 * 12


def test_falls_back_when_no_tool_ever_called():
    # 모델이 곧장 서술만(도구 0회) → 결과 없음 → 원샷 폴백.
    llm = MagicMock()
    llm.chat_tools.return_value = ToolTurn("바로 결론", [], {"role": "assistant"})
    llm.chat.return_value = json.dumps({"pbr": {"target_pbr": 1.0}, "conclusion": "폴백"})
    with _patch_dispatch():
        out = vs.run_valuation(llm, "m", _ctx(), {}, _series())
    assert out["method_count"] >= 1  # 폴백이 방식 계산
