"""범용 기사 크롤러 테스트 — 본문 컨테이너 추출·잡음 제거·폴백·실패 처리."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.external import article_crawler as ac


def _resp(html):
    r = MagicMock()
    r.text = html
    r.raise_for_status = MagicMock()
    return r


def test_extracts_known_container():
    html = """<html><body>
    <h1>가비아 맥쿼리 JV 코어허브 설립</h1>
    <div id="dic_area">가비아가 맥쿼리와 합작법인 코어허브를 세워 4~6년간 6304억원을 안산 IDC 에
    투자한다. KINX 가 최대 수혜자로 기대된다. 이 프로젝트는 100MW 규모다. 추가 설명 문장을 더 넣어
    최소 본문 분량 150자를 확실히 넘기도록 채운다. 데이터센터 인프라 확대의 지렛대가 될 전망이다.</div>
    <script>광고 스크립트</script></body></html>"""
    sess = MagicMock()
    sess.get.return_value = _resp(html)
    out = ac.crawl_article("https://n.news.naver.com/x", sess)
    assert out is not None
    assert "코어허브" in out["body"] and "6304억" in out["body"]
    assert "광고 스크립트" not in out["body"]  # script 제거


def test_returns_none_when_too_short():
    html = '<html><body><div id="dic_area">짧음</div></body></html>'
    sess = MagicMock()
    sess.get.return_value = _resp(html)
    assert ac.crawl_article("https://x", sess) is None


def test_paragraph_fallback():
    body = "문장 " * 100  # 300자 이상
    html = f"<html><body><div><p>{body}</p></div></body></html>"
    sess = MagicMock()
    sess.get.return_value = _resp(html)
    out = ac.crawl_article("https://x", sess)
    assert out is not None and len(out["body"]) > 150


def test_network_error_returns_none():
    import requests
    sess = MagicMock()
    sess.get.side_effect = requests.RequestException("boom")
    assert ac.crawl_article("https://x", sess) is None
