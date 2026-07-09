"""judal 스크래퍼 파싱 단위 테스트 — HTML 픽스처로 네트워크 없이 검증."""

from __future__ import annotations

from unittest.mock import MagicMock

from reporter import judal

_MAIN_HTML = """
<html><body>
<a href="https://www.judal.co.kr/?view=stockList&themeIdx=39">2차전지(22)</a>
<a href="https://www.judal.co.kr/?view=stockList&themeIdx=46">반도체(22)</a>
<a href="https://www.judal.co.kr/?view=stockList&themeIdx=100">전자결제</a>
<a href="/?view=intro">소개</a>
</body></html>
"""

_THEME_HTML = """
<html><body>
<h1 class="fs-5">전자결제 테마주</h1>
<table><tbody>
<tr><th><a href="https://finance.naver.com/item/main.nhn?code=060250" class="btn">
NHN KCP
    KOSDAQ 060250</a></th></tr>
<tr><th><a href="https://finance.naver.com/item/main.nhn?code=064260" class="btn">
다날
    KOSDAQ 064260</a></th></tr>
</tbody></table>
</body></html>
"""


def _session(text: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = text
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_themes_parses_name_and_count():
    themes = judal.fetch_themes(_session(_MAIN_HTML))
    by_idx = {t.idx: t for t in themes}
    assert by_idx[39].name == "2차전지" and by_idx[39].stock_count == 22
    assert by_idx[46].name == "반도체"
    # 종목수 접미사 없는 테마는 count 0.
    assert by_idx[100].name == "전자결제" and by_idx[100].stock_count == 0
    # 테마 링크가 아닌 링크(intro)는 제외.
    assert all(t.idx in (39, 46, 100) for t in themes)


def test_fetch_theme_stocks_extracts_code_and_name():
    ts = judal.fetch_theme_stocks(100, _session(_THEME_HTML))
    assert ts.name == "전자결제"  # '테마주' 접미사 제거
    assert ts.stocks == [("060250", "NHN KCP"), ("064260", "다날")]


def test_fetch_themes_empty_on_error():
    session = MagicMock()
    import requests

    session.get.side_effect = requests.RequestException("boom")
    assert judal.fetch_themes(session) == []
