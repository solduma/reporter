"""웹서치 파이프라인 — 날짜 정규화·관련성 필터·제목 dedup·재랭킹·seen 추적."""

from __future__ import annotations

from app.adapters.external import naver_search
from app.adapters.external.naver_search import SearchHit
from app.services.deepdive import websearch as ws


def _hit(title, desc="", post_date="", source="news"):
    return SearchHit(
        title=title, link="http://x/" + title, description=desc, source=source, post_date=post_date
    )


# ── 날짜 정규화 ──────────────────────────────────────────────────────────
def test_norm_date_blog_yyyymmdd():
    assert naver_search._norm_date("20260528") == "20260528"


def test_norm_date_news_rfc822():
    assert naver_search._norm_date("Mon, 26 May 2026 09:24:00 +0900") == "20260526"


def test_norm_date_invalid_empty():
    assert naver_search._norm_date("") == ""
    assert naver_search._norm_date("garbage") == ""


# ── 관련성(제목·스니펫에 alias) ─────────────────────────────────────────
def test_relevance_title_and_snippet():
    # the bell 케이스: 제목엔 가비아만, 스니펫에 종목 언급 → 관련(2).
    assert ws._relevance("가비아 맥쿼리 매각", "케이아이엔엑스 지분 포함", ["케이아이엔엑스", "가비아"]) == 2


def test_relevance_none_when_unrelated():
    assert ws._relevance("삼성 반도체 뉴스", "메모리 시황", ["케이아이엔엑스", "가비아"]) == 0


# ── 재랭킹·dedup·seen ────────────────────────────────────────────────────
def test_rerank_dedup_filters_and_orders():
    hits = [
        _hit("KINX 과천 증설", post_date="20260520"),
        _hit("KINX 과천 증설", post_date="20260520"),  # 중복 제목
        _hit("가비아 맥쿼리 매각", "KINX 지분 포함", post_date="20260528"),  # 스니펫 관련·최신
        _hit("무관 반도체", "삼성", post_date="20260527"),  # 무관 → 제외
    ]
    seen: set[str] = set()
    out = ws._rerank_dedup(hits, ["KINX", "가비아"], seen, recency_weight=0.4)
    titles = [h.title for h in out]
    assert "무관 반도체" not in titles  # 관련성 0 제외
    assert titles.count("KINX 과천 증설") == 1  # 제목 dedup
    assert titles[0] == "가비아 맥쿼리 매각"  # recency_weight 0.4 → 최신 상위


def test_rerank_seen_across_calls():
    seen: set[str] = set()
    ws._rerank_dedup([_hit("KINX 증설", post_date="20260520")], ["KINX"], seen, 0.4)
    # 두 번째 호출: 이미 본 제목이면 제외(단계 간 중복 방지).
    out = ws._rerank_dedup([_hit("KINX 증설", post_date="20260520")], ["KINX"], seen, 0.4)
    assert out == []


def test_rerank_recency_weight_zero_prefers_accuracy():
    # recency_weight=0 이면 원 정렬(정확도 순위) 우선 — 최신이 아니라 앞 index 가 상위.
    hits = [
        _hit("KINX 정확도상위", "KINX", post_date="20260101"),  # index 0(정확도 상위)·오래됨
        _hit("KINX 정확도하위", "KINX", post_date="20260528"),  # index 1·최신
    ]
    out = ws._rerank_dedup(hits, ["KINX"], set(), recency_weight=0.0)
    assert out[0].title == "KINX 정확도상위"
