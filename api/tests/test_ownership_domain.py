"""소유변동 순수 도메인(summarize_ownership) 단위 테스트 — 방향·수량·사유 표현 검증."""

from __future__ import annotations

from app.domain.disclosure import OwnershipChange, summarize_ownership


def test_summarize_acquisition_shows_positive_and_verb():
    change = OwnershipChange(
        reporter="윤원일", position="사장", is_registered="비등기임원",
        shares_after=9214, shares_delta=3000, reason="장내매수",
    )
    out = summarize_ownership(change)
    assert "+3,000주 취득" in out
    assert "변동후 9,214주" in out
    assert "윤원일" in out and "장내매수" in out


def test_summarize_disposal_shows_negative():
    change = OwnershipChange(
        reporter="최준기", position="담당", is_registered="비등기임원",
        shares_after=2705, shares_delta=-1500, reason="장내매도",
    )
    out = summarize_ownership(change)
    assert "-1,500주 처분" in out
    assert "장내매도" in out


def test_summarize_omits_reason_when_empty():
    change = OwnershipChange(
        reporter="A", position="상무", is_registered="", shares_after=100, shares_delta=100,
    )
    out = summarize_ownership(change)
    assert "변동사유" not in out  # 사유 없으면 섹션 생략
