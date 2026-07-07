"""네이버 금융 리서치 페이지 크롤러.

페이지는 EUC-KR 이며 head 의 <meta charset=utf-8> 는 잘못된 값이므로 강제로 euc-kr 로 디코딩한다.
카테고리별로 컬럼 수가 5/6 으로 다르므로 td.file 을 기준점으로 파싱해 두 레이아웃을 모두 처리한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .models import Report

logger = logging.getLogger(__name__)

BASE = "https://finance.naver.com/research"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_MAX_PAGES = 20  # 당일 리포트만 필요하므로 안전 상한


def _fetch_list_page(category: str, page: int, session: requests.Session) -> list[Report]:
    url = f"{BASE}/{category}_list.naver?&page={page}"
    resp = session.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = "euc-kr"  # head 의 utf-8 meta 는 무시
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", class_="type_1")
    if table is None:
        return []

    reports: list[Report] = []
    for tr in table.find_all("tr"):
        dates = tr.find_all("td", class_="date")
        if len(dates) < 2:  # 헤더/빈칸/구분선 행 제외
            continue

        title_a = tr.find("a", href=lambda h: h and "_read.naver" in h)
        file_td = tr.find("td", class_="file")
        pdf_a = file_td.find("a") if file_td else None
        broker_td = file_td.find_previous_sibling("td") if file_td else None
        stock_a = tr.find("a", class_="stock_item")

        read_url = title_a["href"] if title_a else None
        if read_url and read_url.startswith(("company_", "industry_", "market_info_",
                                             "invest_", "economy_", "debenture_")):
            read_url = f"{BASE}/{read_url}"

        stock_code = None
        if stock_a and "code=" in stock_a.get("href", ""):
            stock_code = stock_a["href"].split("code=")[-1]

        reports.append(
            Report(
                category=category,
                title=title_a.get_text(strip=True) if title_a else "(제목 없음)",
                broker=broker_td.get_text(strip=True) if broker_td else "",
                date=dates[0].get_text(strip=True),
                views=int(dates[1].get_text(strip=True) or 0),
                read_url=read_url,
                pdf_url=pdf_a["href"] if pdf_a else None,
                stock_name=stock_a.get_text(strip=True) if stock_a else None,
                stock_code=stock_code,
            )
        )
    return reports


def crawl_category(category: str, session: requests.Session | None = None) -> list[Report]:
    """오늘 발행된 리포트만 수집. 목록은 최신순이므로 과거 날짜가 나오면 페이징을 멈춘다."""
    session = session or requests.Session()
    today = datetime.now().strftime("%y.%m.%d")
    collected: list[Report] = []

    for page in range(1, _MAX_PAGES + 1):
        batch = _fetch_list_page(category, page, session)
        if not batch:
            break
        todays = [r for r in batch if r.date == today]
        collected.extend(todays)
        if len(todays) < len(batch):  # 이 페이지에 과거 날짜 존재 → 이후 페이지는 모두 과거
            break

    logger.info("crawled %s: %d today's reports", category, len(collected))
    return collected


def crawl_categories(categories: list[str]) -> list[Report]:
    session = requests.Session()
    reports: list[Report] = []
    for category in categories:
        try:
            reports.extend(crawl_category(category, session))
        except requests.RequestException as e:
            logger.warning("failed to crawl %s: %s", category, e)
    return reports
