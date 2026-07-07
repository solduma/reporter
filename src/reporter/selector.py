"""핵심 리포트 선별 — 조회수 정규화 + 주요 증권사 보너스."""

from __future__ import annotations

from .models import Report

MAJOR_BROKERS = {
    "삼성증권", "미래에셋증권", "KB증권", "NH투자증권", "한국투자증권",
    "신한투자증권", "키움증권", "하나증권", "대신증권", "메리츠증권",
    "IBK투자증권", "교보증권", "유안타증권", "현대차증권", "LS증권",
}

_MAJOR_BONUS = 30.0


def _score(report: Report, max_views: int) -> float:
    view_score = (report.views / max_views * 100) if max_views else 0.0
    bonus = _MAJOR_BONUS if report.broker in MAJOR_BROKERS else 0.0
    return view_score + bonus


def select_top(reports: list[Report], top_n: int = 5) -> list[Report]:
    """카테고리별로 조회수·증권사 점수 상위 top_n 개를 선별한다."""
    by_category: dict[str, list[Report]] = {}
    for r in reports:
        by_category.setdefault(r.category, []).append(r)

    selected: list[Report] = []
    for group in by_category.values():
        max_views = max((r.views for r in group), default=0)
        for r in group:
            r.score = _score(r, max_views)
        group.sort(key=lambda r: r.score, reverse=True)
        selected.extend(group[:top_n])
    return selected
