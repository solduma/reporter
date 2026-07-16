"""이벤트 키워드 도메인 테스트 — 공통+섹터 병합·중복제거·쿼리 생성."""

from __future__ import annotations

from app.domain import event_keywords as ek


def test_common_only_when_sector_unknown():
    kw = ek.for_sector(None)
    assert "수주" in kw.catalysts and "소송" in kw.risks
    assert kw.sector is None
    # 공시 필터는 항상 공통 제공.
    assert "공급계약" in kw.disclosure_filters


def test_sector_keywords_prepended():
    # 반도체 특화(HBM·증설)가 공통보다 앞에(우선순위).
    kw = ek.for_sector("반도체")
    assert "HBM" in kw.catalysts
    assert kw.catalysts.index("HBM") < kw.catalysts.index("수주")  # 섹터 특화가 앞


def test_bio_risk_keywords():
    kw = ek.for_sector("바이오")
    assert "기술수출" in kw.catalysts
    assert "임상 실패" in kw.risks and "임상 중단" in kw.risks


def test_dedup_no_duplicates():
    # 섹터가 공통과 겹치는 키워드(예 조선 '수주')를 중복 없이 병합.
    kw = ek.for_sector("조선")
    assert kw.catalysts.count("수주") == 1
    assert len(kw.catalysts) == len(set(kw.catalysts))
    assert len(kw.risks) == len(set(kw.risks))


def test_search_queries_both_sides():
    kw = ek.for_sector("건설")
    qs = ek.search_queries("힐스테이트", kw, per_side=3)
    assert all(q.startswith("힐스테이트 ") for q in qs)
    # 촉매(수주/분양 계열)와 리스크(미분양/PF 계열)가 모두 포함.
    joined = " ".join(qs)
    assert any(k in joined for k in ("수주", "분양"))
    assert any(k in joined for k in ("미분양", "PF"))
    assert len(qs) == len(set(qs))  # 중복 없음


def test_search_queries_respects_per_side():
    kw = ek.for_sector("반도체")
    qs = ek.search_queries("삼성전자", kw, per_side=4)
    assert len(qs) <= 8  # 촉매 4 + 리스크 4


def test_all_sectors_have_both_sides():
    # 등록된 모든 섹터가 촉매·리스크 둘 다 비어있지 않아야(한쪽만 검색되는 편향 방지).
    for sector in ek._SECTOR:
        kw = ek.for_sector(sector)
        assert kw.catalysts and kw.risks, f"{sector} 한쪽 비어있음"
