from datetime import UTC
from unittest.mock import MagicMock

import requests

from reporter import news

_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>\xec\x82\xbc\xec\x84\xb1\xec\xa0\x84\xec\x9e\x90 \xec\x8b\xa0\xea\xb3\xa0\xea\xb0\x80</title>
    <link>http://news/1</link>
    <source url="http://yna.co.kr">\xec\x97\xb0\xed\x95\xa9\xeb\x89\xb4\xec\x8a\xa4</source>
  </item>
  <item>
    <title>\xeb\x91\x90\xeb\xb2\x88\xec\xa7\xb8 \xea\xb8\xb0\xec\x82\xac</title>
    <link>http://news/2</link>
    <source url="http://hankyung.com">\xed\x95\x9c\xea\xb5\xad\xea\xb2\xbd\xec\xa0\x9c</source>
  </item>
</channel></rss>"""


def _session_returning(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_parses_items_with_title_and_source():
    session = _session_returning(_RSS_XML)
    items = news.search("삼성전자", session=session)

    assert len(items) == 2
    assert items[0].title == "삼성전자 신고가"
    assert items[0].source == "연합뉴스"
    assert items[0].link == "http://news/1"
    assert items[1].source == "한국경제"


def test_respects_limit():
    session = _session_returning(_RSS_XML)
    items = news.search("삼성전자", limit=1, session=session)
    assert len(items) == 1


def test_network_error_returns_empty_list():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("boom")
    assert news.search("삼성전자", session=session) == []


def test_malformed_xml_returns_empty_list():
    session = _session_returning(b"<rss><not-closed>")
    assert news.search("삼성전자", session=session) == []


def test_query_is_url_encoded():
    session = _session_returning(_RSS_XML)
    news.search("삼성 전자", session=session)
    url = session.get.call_args.args[0]
    # 공백이 인코딩되어 그대로 노출되지 않는다
    assert "삼성 전자" not in url
    assert "%" in url


# ── pubDate 파싱 + 최신성 필터·정렬 (장중 시황 최신성 회귀 가드) ──────────

_RSS_DATED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>old</title><link>http://n/old</link>
    <pubDate>Wed, 15 Jul 2026 00:00:00 GMT</pubDate></item>
  <item><title>fresh</title><link>http://n/fresh</link>
    <pubDate>Wed, 15 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_search_parses_pubdate():
    session = _session_returning(_RSS_DATED)
    items = news.search("증시", session=session)
    assert items[0].published_at is not None
    assert items[0].published_at.tzinfo is not None  # tz-aware
    # 두 기사의 시각이 다르게 파싱됨
    assert items[0].published_at != items[1].published_at


def test_collect_sorts_newest_first_and_filters_old(monkeypatch):
    from datetime import datetime
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)

    old = news.NewsItem("old", "s", "l1", published_at=datetime(2026, 7, 15, 0, 0, tzinfo=UTC))
    fresh = news.NewsItem("fresh", "s", "l2", published_at=datetime(2026, 7, 15, 9, 30, tzinfo=UTC))
    monkeypatch.setattr(news, "search", lambda kw, limit=5, session=None: [old, fresh])

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(news, "datetime", _FixedDatetime)

    # 필터 없음: 최신순 정렬(fresh 먼저)
    out = news.collect(["증시"], 10)
    assert [i.title for i in out] == ["fresh", "old"]

    # 최근 2시간 필터: old(10시간 전) 제외, fresh(30분 전) 유지
    out2 = news.collect(["증시"], 10, max_age_hours=2)
    assert [i.title for i in out2] == ["fresh"]
