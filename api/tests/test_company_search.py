"""종목 검색 랭킹 순수 로직 단위 테스트."""

from __future__ import annotations

from app.routers.companies import _search_rank


def test_exact_code_match_ranks_first():
    assert _search_rank("005930", "005930", "삼성전자") == 0


def test_code_prefix_beats_name_prefix():
    assert _search_rank("00", "005930", "삼성전자") == 1  # 코드 prefix
    assert _search_rank("삼성", "005930", "삼성전자") == 2  # 이름 prefix


def test_name_substring_is_lowest():
    # 접두어가 아닌 부분일치(이름 중간 매칭)는 최하위 랭크.
    assert _search_rank("전자", "005930", "삼성전자") == 3


def test_ranking_order_sorts_by_rank_then_cap():
    # 엔드포인트 정렬 키(랭크 오름차순, 시총 내림차순)를 재현해 순서를 검증.
    rows = [
        ("005935", "삼성전자우", "KOSPI", 60),  # 이름 prefix(삼성) 랭크 2
        ("005930", "삼성전자", "KOSPI", 500),  # 이름 prefix 랭크 2, 시총 최대
        ("999999", "무관종목", "KOSDAQ", 10),  # 매칭 안 됨(부분일치도 아님) 랭크 3
    ]
    q = "삼성"
    ordered = sorted(rows, key=lambda r: (_search_rank(q, r[0], r[1]), -(r[3] or 0)))
    # 삼성전자(시총 큼)가 삼성전자우보다 앞, 무관종목이 맨 뒤.
    assert [r[0] for r in ordered] == ["005930", "005935", "999999"]
