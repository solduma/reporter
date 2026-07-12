"""LLM 종합 코멘트 맥락 주입 + coarse 해시 안정성 단위 테스트."""

from __future__ import annotations

from app.services import analysis, analysis_comment


class _CaptureLLM:
    """chat 입력(system/user)을 캡처하는 fake LLMPort."""

    def __init__(self):
        self.user = ""

    def chat(self, model, system, user, temperature=0.3):
        self.user = user
        return "요약 결과"


_AXES = [{"key": "growth", "label": "성장", "score": 70, "metrics": []}]


def test_comment_includes_market_and_qualitative_context():
    llm = _CaptureLLM()
    ctx = analysis.CommentContext(
        market_phase="intraday",
        market_summary="코스피 강세, 반도체 주도",
        report_count=5,
        buy_count=3,
        recent_disclosures=["단일판매공급계약", "유상증자결정"],
    )
    out = analysis.llm_comment(llm, "m", "삼성전자", _AXES, ctx)
    assert out == "요약 결과"
    # 프롬프트에 시장 국면·시황·리포트·공시가 실렸는지.
    assert "장중" in llm.user
    assert "코스피 강세" in llm.user
    assert "BUY 3건" in llm.user
    assert "단일판매공급계약" in llm.user


def test_comment_without_context_still_works():
    llm = _CaptureLLM()
    out = analysis.llm_comment(llm, "m", "삼성전자", _AXES, None)
    assert out == "요약 결과"
    assert "[시장 맥락]" not in llm.user  # 맥락 없으면 섹션 미포함


def test_hash_stable_across_intraday_summary_change():
    # 시황 요약 원문이 장중에 바뀌어도(같은 국면·리포트수) 해시는 동일해야 재생성 폭주가 없다.
    ctx1 = analysis.CommentContext(
        market_phase="intraday", market_summary="10:00 시황...", report_count=5, buy_count=3
    )
    ctx2 = analysis.CommentContext(
        market_phase="intraday", market_summary="14:00 완전히 다른 시황...", report_count=5, buy_count=3
    )
    assert analysis_comment.inputs_hash(_AXES, ctx1) == analysis_comment.inputs_hash(_AXES, ctx2)


def test_hash_changes_when_phase_or_coverage_changes():
    base = analysis.CommentContext(market_phase="intraday", report_count=5, buy_count=3)
    diff_phase = analysis.CommentContext(market_phase="closing", report_count=5, buy_count=3)
    diff_buys = analysis.CommentContext(market_phase="intraday", report_count=5, buy_count=4)
    h = analysis_comment.inputs_hash(_AXES, base)
    assert h != analysis_comment.inputs_hash(_AXES, diff_phase)
    assert h != analysis_comment.inputs_hash(_AXES, diff_buys)


def test_hash_context_none_differs_from_context_present():
    assert analysis_comment.inputs_hash(_AXES, None) != analysis_comment.inputs_hash(
        _AXES, analysis.CommentContext(market_phase="intraday")
    )
