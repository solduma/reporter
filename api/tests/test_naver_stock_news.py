"""네이버 증권 종목 뉴스 어댑터 테스트 — JSON 파싱·중복제거·페이지네이션."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.adapters.external import naver_stock_news as sn


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _item(aid, title, body="요약", press="한국경제", dt="202606010700"):
    return {"articleId": aid, "officeId": "015", "officeName": press, "datetime": dt,
            "title": title, "titleFull": title, "body": body,
            "mobileNewsUrl": f"https://n.news.naver.com/mnews/article/015/{aid}"}


def test_parses_items():
    payload = [{"total": 2, "items": [_item("1", "수주 공시"), _item("2", "실적 발표")]}]
    sess = MagicMock()
    sess.get.return_value = _resp(payload)
    news = sn.fetch_stock_news("093320", sess, pages=1)
    assert len(news) == 2
    assert news[0].title == "수주 공시" and news[0].press == "한국경제"
    assert news[0].url.endswith("/015/1")


def test_dedup_across_pages():
    # 같은 URL 이 여러 페이지에 나와도 1회만.
    payload = [{"total": 1, "items": [_item("1", "동일 기사")]}]
    sess = MagicMock()
    sess.get.return_value = _resp(payload)
    news = sn.fetch_stock_news("093320", sess, pages=3)
    assert len(news) == 1


def test_empty_items_stops():
    sess = MagicMock()
    sess.get.return_value = _resp([{"total": 0, "items": []}])
    assert sn.fetch_stock_news("093320", sess, pages=2) == []


def test_network_error_returns_partial():
    import requests
    sess = MagicMock()
    sess.get.side_effect = requests.RequestException("timeout")
    assert sn.fetch_stock_news("093320", sess) == []
