"""섹터 매핑 단위 테스트 — themes_to_kr_sector 멱등성 회귀 방지.

종목 상세가 topdown.kr_sector(이미 폴딩된 확정 섹터명)를 섹터 차트에 다시 넘기면
키워드 부분일치가 확정명을 재폴딩해 엉뚱한 섹터로 가던 버그(필수소비재→에너지화학,
로봇→기계장비, 경기소비재→None) 회귀 방지.
"""

from __future__ import annotations

import pytest

from reporter import sector_etf


@pytest.mark.parametrize("sector", [e.sector for e in sector_etf.KR_SECTOR_ETFS])
def test_canonical_sector_is_idempotent(sector):
    # 확정 섹터명을 넣으면 그대로 나와야 한다(재폴딩 금지).
    assert sector_etf.themes_to_kr_sector([sector]) == sector


def test_raw_theme_names_still_fold():
    # 원시 judal 테마명은 기존대로 대표 섹터로 폴딩된다.
    assert sector_etf.themes_to_kr_sector(["반도체 장비"]) == "반도체 소부장"
    assert sector_etf.themes_to_kr_sector(["2차전지 소재"]) == "2차전지"
    assert sector_etf.themes_to_kr_sector(["해운"]) == "운송"


def test_unmatched_returns_none():
    assert sector_etf.themes_to_kr_sector(["존재하지않는테마"]) is None
