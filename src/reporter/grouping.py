"""리포트를 종목/산업 단위로 묶는다. 순수 함수(I/O 없음)."""

from __future__ import annotations

from .models import Report


def group_by_entity(reports: list[Report]) -> dict[str, list[Report]]:
    """company 는 종목명, industry 는 업종명 기준으로 묶는다.

    키가 없으면(종목명·업종명 미상) 제목으로 폴백해 최소한 개별 그룹은 유지한다.
    삽입 순서(조회수 정렬 등 호출측 정렬)를 보존한다.
    """
    groups: dict[str, list[Report]] = {}
    for r in reports:
        key = r.stock_name if r.category == "company" else r.industry
        key = (key or r.title).strip()
        groups.setdefault(key, []).append(r)
    return groups
