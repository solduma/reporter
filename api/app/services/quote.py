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
    period_type: str = "quarter"  # 'annual' | 'quarter' — 연간/분기 컬럼이 섞여 나온다
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


def _period_types(section) -> list[str]:
    """헤더 상단 그룹 행의 colspan(연간 N / 분기 M)으로 각 기간 컬럼의 유형을 정한다.

    main.naver 는 연간·분기 기간을 한 테이블에 섞어 주고 '2025.12' 처럼 양쪽에
    중복 등장하는 기간이 있으므로, 유형을 반드시 구분해야 키 충돌·차트 혼선을 막는다.
    """
    for tr in section.select("thead tr"):
        labels = [(th.get_text(strip=True), th.get("colspan")) for th in th_cells(tr)]
        annual = next((int(c) for t, c in labels if "연간" in t and c), 0)
        quarter = next((int(c) for t, c in labels if "분기" in t and c), 0)
        if annual or quarter:
            return ["annual"] * annual + ["quarter"] * quarter
    return []


def th_cells(tr):
    return tr.find_all("th")


def fetch_financials(code: str, session: requests.Session) -> list[FinancialPeriod]:
    """분기 실적만 반환한다(요구사항: 분기별 지표). 연간 컬럼은 유형으로 구분해 제외."""
    soup = _fetch_soup(code, session)
    if soup is None:
        return []
    section = soup.select_one("div.cop_analysis")
    if section is None:
        return []

    period_cells = [th.get_text(strip=True) for th in section.select("thead th")]
    periods = [p for p in period_cells if re.match(r"\d{4}\.\d{2}", p)]
    types = _period_types(section)
    if not periods or len(types) != len(periods):
        return []  # 헤더 구조가 예상과 다르면 잘못 매핑하느니 비운다

    records = [
        FinancialPeriod(period=p, is_estimate="(E)" in p or "(e)" in p, period_type=t)
        for p, t in zip(periods, types, strict=True)
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

    # 요구사항은 분기별 지표. 연간 컬럼은 제외해 축·기간 충돌을 없앤다.
    return [r for r in records if r.period_type == "quarter"]


def fetch_shares_outstanding(code: str, session: requests.Session) -> int | None:
    """네이버 종목 페이지의 현재 상장주식수(주). PER/PBR/PSR 역산용. 실패 시 None.

    과거 시점 주식수는 제공하지 않아 현재값을 쓴다(과거 증자·자사주 변동은 근사로 미반영).
    """
    soup = _fetch_soup(code, session)
    if soup is None:
        return None
    for table in soup.select("table"):
        text = table.get_text(" ", strip=True)
        m = re.search(r"상장주식수\s*([\d,]+)", text)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


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
    for idx, cell in enumerate(head_cells):
        name, sep, cd = cell.partition("*")
        # '*' 로 코드를 못 뽑으면 base 코드로 넘기지 않는다(uq_peer 충돌·기준종목 오강조 방지).
        peer_code = cd.strip() if sep else f"?{idx}"
        peers.append(Peer(stock_code=peer_code, name=name.strip()))
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
