"""네이버 종목 페이지(main.naver) 스크래핑 — 분기 재무 + 동일업종비교.

리서치 목록은 EUC-KR 이지만 main.naver 는 UTF-8 이라 별도 모듈로 분리한다.
- 재무: div.cop_analysis 의 '주요재무정보' 테이블. 헤더에 연간+분기 기간, 행에 항목.
- 동일업종: div.section.trade_compare. 컬럼=종목, 행=지표.
개인 리서치 용도, 조회 시 캐시로 호출 최소화.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_URL = "https://finance.naver.com/item/main.naver?code={code}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; reporter-bot/1.0)"}

# main.naver 재무 행 라벨 → 우리 필드명
_FIN_ROWS = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "EPS(원)": "eps",
    "PER(배)": "per",
    "BPS(원)": "bps",
    "PBR(배)": "pbr",
    "ROE(지배주주)": "roe",
}


@dataclass
class FinancialPeriod:
    period: str  # 예 '2026.03' 또는 '2026.12(E)'
    is_estimate: bool
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps: float | None = None
    bps: float | None = None
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None


@dataclass
class Peer:
    stock_code: str
    name: str
    values: dict[str, str] = field(default_factory=dict)  # 행라벨 → 표시문자열


def _num(text: str) -> float | None:
    """'2,589,355' / '-5.83' / '' → float|None. 방향표시·기호 제거."""
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fetch_soup(code: str, session: requests.Session) -> BeautifulSoup | None:
    try:
        resp = session.get(_URL.format(code=code), headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("main.naver fetch failed %s: %s", code, e)
        return None
    resp.encoding = "utf-8"  # main.naver 는 UTF-8 (리서치 목록의 euc-kr 과 다름)
    return BeautifulSoup(resp.text, "html.parser")


def fetch_financials(code: str, session: requests.Session) -> list[FinancialPeriod]:
    """분기 실적 우선으로 재무 기간별 지표를 반환한다(연간 포함 전체)."""
    soup = _fetch_soup(code, session)
    if soup is None:
        return []
    section = soup.select_one("div.cop_analysis")
    if section is None:
        return []

    # 헤더의 기간 컬럼(연간 + 분기). '주요재무정보' 이후의 기간 텍스트만.
    period_cells = [th.get_text(strip=True) for th in section.select("thead th")]
    periods = [p for p in period_cells if re.match(r"\d{4}\.\d{2}", p)]
    if not periods:
        return []

    records = [
        FinancialPeriod(period=p, is_estimate="(E)" in p or "(e)" in p) for p in periods
    ]

    for tr in section.select("tbody tr"):
        th = tr.find("th")
        if not th:
            continue
        label = th.get_text(strip=True)
        field_name = _FIN_ROWS.get(label)
        if not field_name:
            continue
        values = [_num(td.get_text(strip=True)) for td in tr.find_all("td")]
        for rec, val in zip(records, values, strict=False):
            setattr(rec, field_name, val)

    return records


def fetch_peers(code: str, session: requests.Session) -> list[Peer]:
    """동일업종비교 테이블 — 종목별 지표."""
    soup = _fetch_soup(code, session)
    if soup is None:
        return []
    section = soup.select_one("div.section.trade_compare")
    if section is None:
        return []

    # 헤더: '종목명' 다음이 각 종목 (이름*코드 형태)
    head_cells = [th.get_text(strip=True) for th in section.select("thead th")][1:]
    peers: list[Peer] = []
    for cell in head_cells:
        name, _, cd = cell.partition("*")
        peers.append(Peer(stock_code=cd.strip() or code, name=name.strip()))
    if not peers:
        return []

    for tr in section.select("tbody tr"):
        th = tr.find("th")
        if not th:
            continue
        label = th.get_text(strip=True)
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        for peer, cell in zip(peers, cells, strict=False):
            # 방향 접두어(상향/하향) 제거해 값만 남긴다
            peer.values[label] = cell.replace("상향", "").replace("하향", "").strip()

    return peers
