"""시황 분류 단위 테스트 — 국내 마감시황 판별·오전/마감 분리.

전일 국내 마감시황이 오늘 오전 브리핑에 섞이던 오염(issue #43) 회귀 방지.
"""

from __future__ import annotations

import pytest

from reporter.market import is_domestic_closing, split_by_closing
from reporter.models import Report


def _report(title: str) -> Report:
    return Report(category="market_info", title=title, broker="b", date="26.07.09", views=1)


@pytest.mark.parametrize(
    "title",
    [
        "국내주식 마감 시황 (26.07.08) - 모두가 아는 한국만",
        "국내 주식 마감 시황 - 주요 지지 이탈+반대매매에 사이드카",
        "7/8 KB 리서치 장마감코멘트",
        # 해외를 원인으로 언급하지만 대상은 국내 마감 — 국내 신호 우선(오염 방지)
        "뉴욕發 훈풍에 코스피 마감 강세",
        "미국 CPI 경계 속 코스피 마감 약세",
    ],
)
def test_domestic_closing_titles(title):
    assert is_domestic_closing(_report(title)) is True


@pytest.mark.parametrize(
    "title",
    [
        "유안타 AI 미국 주식시장 마감 시황 (26.07.09)",  # 미국 → 오늘 아침에 유효
        "뉴욕증시 마감 코멘트",
        "Daily Morning Brief(2026.07.09)",
        "Yuanta Morning Snapshot (2026.07.09)",
        "[Morning Brief] 반도체 업종 중심 저가매수세 유입",
        "방어주도 방어 실패",
        # '마감' 오탐 방지: 아래는 마감시황이 아님(테마 감소·감독·감안 등)
        "AI 테마 감소, 방어주 부각",
        "드라마 감독株 급등",
        "반도체 테마 감소 속 관망",
        "실적 테마 감안한 밸류에이션",
    ],
)
def test_non_domestic_closing_titles(title):
    assert is_domestic_closing(_report(title)) is False


def test_split_separates_morning_and_closing():
    reports = [
        _report("Daily Morning Brief(2026.07.09)"),
        _report("국내주식 마감 시황 (26.07.08)"),
        _report("유안타 AI 미국 주식시장 마감 시황 (26.07.09)"),
        _report("7/8 KB 리서치 장마감코멘트"),
    ]
    morning, closing = split_by_closing(reports)

    morning_titles = [r.title for r in morning]
    closing_titles = [r.title for r in closing]
    # 오전: 모닝브리프 + 미국 마감(유지)
    assert "Daily Morning Brief(2026.07.09)" in morning_titles
    assert "유안타 AI 미국 주식시장 마감 시황 (26.07.09)" in morning_titles
    # 마감: 국내 마감시황 2건
    assert set(closing_titles) == {"국내주식 마감 시황 (26.07.08)", "7/8 KB 리서치 장마감코멘트"}


def test_split_preserves_all_reports():
    reports = [_report("국내주식 마감 시황"), _report("Morning Brief")]
    morning, closing = split_by_closing(reports)
    assert len(morning) + len(closing) == len(reports)  # 유실 없음


def test_split_empty():
    assert split_by_closing([]) == ([], [])
