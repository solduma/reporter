from datetime import datetime
from unittest.mock import MagicMock

import reporter.crawler as crawler

# 6-column company 레이아웃 (종목명 링크 포함) + 5-column market_info 레이아웃 재현
_COMPANY_HTML = """
<table class="type_1" summary="종목분석 리포트 게시판 글목록">
<tr><th>종목명</th><th>제목</th><th>증권사</th><th>첨부</th><th>작성일</th><th>조회수</th></tr>
<tr><td class="blank_07" colspan="6"></td></tr>
<tr>
  <td><a class="stock_item" href="/item/main.naver?code=139480" title="이마트">이마트</a></td>
  <td><a href="company_read.naver?nid=93952&page=1">이마트 목표주가 상향</a><img class="ico_new"></td>
  <td>삼성증권</td>
  <td class="file"><a href="https://stock.pstatic.net/stock-research/company/16/20260707_company_1.pdf" target="_blank"><img alt="pdf"></a></td>
  <td class="date">{today}</td>
  <td class="date">1895</td>
</tr>
<tr>
  <td><a class="stock_item" href="/item/main.naver?code=005930" title="삼성전자">삼성전자</a></td>
  <td><a href="company_read.naver?nid=93953&page=1">첨부없는 리포트</a></td>
  <td>듣보증권</td>
  <td class="file"></td>
  <td class="date">{today}</td>
  <td class="date">50</td>
</tr>
<tr>
  <td><a class="stock_item" href="/item/main.naver?code=000660" title="SK하이닉스">SK하이닉스</a></td>
  <td><a href="company_read.naver?nid=90000&page=1">어제 리포트</a></td>
  <td>하나증권</td>
  <td class="file"><a href="https://stock.pstatic.net/x.pdf"></a></td>
  <td class="date">25.01.01</td>
  <td class="date">9999</td>
</tr>
</table>
"""


def _mock_session(html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_parses_company_rows_and_filters_today(monkeypatch):
    today = datetime.now().strftime("%y.%m.%d")
    html = _COMPANY_HTML.format(today=today)
    session = _mock_session(html)

    reports = crawler.crawl_category("company", session=session)

    # 오늘 리포트 2건만 (어제 리포트는 제외), 헤더/blank 행도 제외
    assert len(reports) == 2
    first = reports[0]
    assert first.title == "이마트 목표주가 상향"
    assert first.broker == "삼성증권"
    assert first.views == 1895
    assert first.stock_name == "이마트"
    assert first.stock_code == "139480"
    assert first.pdf_url.endswith("20260707_company_1.pdf")
    assert first.read_url.startswith("https://finance.naver.com/research/company_read.naver")


def test_missing_pdf_is_none(monkeypatch):
    today = datetime.now().strftime("%y.%m.%d")
    session = _mock_session(_COMPANY_HTML.format(today=today))
    reports = crawler.crawl_category("company", session=session)
    no_pdf = reports[1]
    assert no_pdf.title == "첨부없는 리포트"
    assert no_pdf.pdf_url is None


def test_stops_paging_when_older_date_present(monkeypatch):
    today = datetime.now().strftime("%y.%m.%d")
    session = _mock_session(_COMPANY_HTML.format(today=today))
    crawler.crawl_category("company", session=session)
    # 어제 날짜 행이 섞여 있으므로 1페이지만 조회하고 멈춘다
    assert session.get.call_count == 1


# 최신순 3행: 최신(26.01.02) → target(26.01.01) → 과거(25.12.31)
_DATED_HTML = """
<table class="type_1">
<tr>
  <td><a href="company_read.naver?nid=1&page=1">최신 리포트</a></td>
  <td>삼성증권</td>
  <td class="file"><a href="https://x/a.pdf"></a></td>
  <td class="date">26.01.02</td><td class="date">10</td>
</tr>
<tr>
  <td><a href="company_read.naver?nid=2&page=1">타겟 리포트</a></td>
  <td>KB증권</td>
  <td class="file"><a href="https://x/b.pdf"></a></td>
  <td class="date">26.01.01</td><td class="date">20</td>
</tr>
<tr>
  <td><a href="company_read.naver?nid=3&page=1">과거 리포트</a></td>
  <td>하나증권</td>
  <td class="file"><a href="https://x/c.pdf"></a></td>
  <td class="date">25.12.31</td><td class="date">30</td>
</tr>
</table>
"""


_INDUSTRY_HTML = """
<table class="type_1" summary="산업분석 리포트 게시판 글목록">
<tr><th>분류</th><th>제목</th><th>증권사</th><th>첨부</th><th>작성일</th><th>조회수</th></tr>
<tr>
  <td>자동차</td>
  <td><a href="industry_read.naver?nid=100&page=1">로보틱스 아젠다</a></td>
  <td>DS투자증권</td>
  <td class="file"><a href="https://x/a.pdf"></a></td>
  <td class="date">{today}</td>
  <td class="date">496</td>
</tr>
<tr>
  <td>반도체</td>
  <td><a href="industry_read.naver?nid=90&page=1">어제 산업 리포트</a></td>
  <td>하나증권</td>
  <td class="file"><a href="https://x/b.pdf"></a></td>
  <td class="date">25.01.01</td>
  <td class="date">10</td>
</tr>
</table>
"""


def test_industry_captures_classification_column():
    # 산업분석 목록의 첫 컬럼('분류')을 industry 로 채워야 한다 (종목명 앵커 없음)
    today = datetime.now().strftime("%y.%m.%d")
    session = _mock_session(_INDUSTRY_HTML.format(today=today))
    reports = crawler.crawl_category("industry", session=session)
    # 오늘 1건만(어제 25.01.01 은 제외), 분류 컬럼이 industry 로 채워짐
    assert len(reports) == 1
    assert reports[0].industry == "자동차"
    assert reports[0].stock_name is None
    assert reports[0].title == "로보틱스 아젠다"


def test_company_does_not_set_industry():
    # 종목분석은 industry 를 채우지 않는다 (분류 컬럼 개념이 다름)
    today = datetime.now().strftime("%y.%m.%d")
    session = _mock_session(_COMPANY_HTML.format(today=today))
    reports = crawler.crawl_category("company", session=session)
    assert all(r.industry is None for r in reports)


def test_target_date_skips_newer_and_collects_only_target():
    # 최신순 목록에서 target 보다 최신 행은 건너뛰고, target 행만 담고, 과거 행 만나 멈춘다
    session = _mock_session(_DATED_HTML)
    reports = crawler.crawl_category("company", session=session, target_date="26.01.01")
    assert len(reports) == 1
    assert reports[0].title == "타겟 리포트"
    assert reports[0].date == "26.01.01"
    # 과거(25.12.31) 행이 있으므로 1페이지만 조회하고 멈춘다
    assert session.get.call_count == 1
