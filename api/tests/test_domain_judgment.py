"""종목 판단 요약 도메인 단위 테스트 — 점수 조합별 신호·강약·확인사항."""

from __future__ import annotations

from app.domain import judgment as j


def test_fit_all_strong():
    r = j.summarize(75, {"growth": 80, "technical": 70, "topdown": 65})
    assert r.signal == "fit" and r.signal_label == "매수 적합"
    assert len(r.strengths) == 3
    assert r.weaknesses == []


def test_avoid_low_overall():
    r = j.summarize(30, {"growth": 35, "technical": 30, "topdown": 25})
    assert r.signal == "avoid"
    assert len(r.weaknesses) == 3
    assert r.strengths == []


def test_avoid_two_weak_axes_even_if_overall_mid():
    # 종합은 중립이라도 약한 축 2개 이상이면 회피.
    r = j.summarize(50, {"growth": 85, "technical": 35, "topdown": 30})
    assert r.signal == "avoid"
    assert len(r.weaknesses) == 2


def test_watch_mixed():
    # 종합이 중립 구간(40~60)이고 약한 축 1개 이하 → 관망.
    r = j.summarize(52, {"growth": 55, "technical": 50, "topdown": 48})
    assert r.signal == "watch" and r.signal_label == "관망"
    # 중립 축들은 확인사항으로 유도.
    assert any("추세" in c for c in r.checks)


def test_fit_requires_no_weakness():
    # 종합 60↑ 이라도 약한 축이 있으면 fit 아님(watch).
    r = j.summarize(61, {"growth": 90, "technical": 90, "topdown": 30})
    assert r.signal == "watch"


def test_insufficient_when_no_scores():
    r = j.summarize(None, {"growth": None, "technical": None, "topdown": None})
    assert r.signal == "insufficient"
    assert r.checks  # 안내 문구 존재


def test_partial_scores_still_summarize():
    # 일부 축만 계산돼도(대형주 성장 None 등) 나머지로 요약.
    r = j.summarize(65, {"growth": None, "technical": 70, "topdown": 62})
    assert r.signal in {"fit", "watch"}
    assert any("추세" in s for s in r.strengths)
