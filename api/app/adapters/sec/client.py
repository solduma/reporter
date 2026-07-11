"""SEC EDGAR 클라이언트 — ticker→CIK 매핑 + companyfacts(XBRL 재무) 조회(driven adapter).

무인증이나 연락처 명시 User-Agent 필수(SEC 정책). ticker 매핑(company_tickers.json)은
전종목 한 파일이라 프로세스 캐시한다. companyfacts 는 종목별 큰 JSON(수 MB)이므로 그대로 반환.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from app.adapters.sec import throttle
from app.config import Settings

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# ticker(대문자) → {cik, name} 프로세스 캐시(전종목 ~9천, 한 파일).
_ticker_map: dict[str, dict] | None = None


def _headers(settings: Settings) -> dict:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def ticker_map(settings: Settings, session: requests.Session | None = None) -> dict[str, dict]:
    """전종목 ticker→{cik:int, name:str} 매핑. 캐시. 실패 시 빈 dict."""
    global _ticker_map
    if _ticker_map is not None:
        return _ticker_map
    session = session or requests.Session()
    try:
        resp = throttle.get(session, _TICKERS_URL, headers=_headers(settings), timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("SEC ticker map fetch failed: %s", e)
        return {}
    # 응답은 {"0": {"cik_str":.., "ticker":.., "title":..}, ...}
    out = {
        row["ticker"].upper(): {"cik": int(row["cik_str"]), "name": row["title"]}
        for row in data.values()
        if row.get("ticker")
    }
    _ticker_map = out
    return out


def resolve_cik(settings: Settings, ticker: str, session: requests.Session | None = None) -> int | None:
    """ticker(대소문자 무관) → CIK. 없으면 None."""
    return (ticker_map(settings, session).get(ticker.upper()) or {}).get("cik")


def company_name(settings: Settings, ticker: str, session: requests.Session | None = None) -> str | None:
    return (ticker_map(settings, session).get(ticker.upper()) or {}).get("name")


def fetch_company_facts(
    settings: Settings, cik: int, session: requests.Session | None = None
) -> dict | None:
    """CIK 의 companyfacts(XBRL 전체 재무 사실) JSON. 실패·없음이면 None."""
    session = session or requests.Session()
    try:
        resp = throttle.get(
            session, _FACTS_URL.format(cik=cik), headers=_headers(settings), timeout=30
        )
        if resp.status_code == 404:
            return None  # 해당 CIK 재무사실 없음
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("SEC companyfacts fetch failed CIK%s: %s", cik, e)
        return None


@dataclass
class Filing:
    accession: str
    form: str  # 8-K | 10-K ...
    filing_date: str  # YYYY-MM-DD
    items: str  # 8-K item 코드(예 '5.02,7.01'), 없으면 ''
    primary_doc_url: str


def fetch_recent_filings(
    settings: Settings, cik: int, forms: tuple[str, ...] = ("8-K",),
    limit: int = 20, session: requests.Session | None = None,
) -> list[Filing]:
    """CIK 의 최근 공시(기본 8-K) 목록. submissions API 의 recent 배열을 파싱. 실패 시 빈 리스트."""
    session = session or requests.Session()
    try:
        resp = throttle.get(
            session, _SUBMISSIONS_URL.format(cik=cik), headers=_headers(settings), timeout=20
        )
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})
    except (requests.RequestException, ValueError) as e:
        logger.warning("SEC submissions fetch failed CIK%s: %s", cik, e)
        return []
    forms_set = set(forms)
    out: list[Filing] = []
    accs = recent.get("accessionNumber", [])
    for i in range(len(accs)):
        if recent["form"][i] not in forms_set:
            continue
        acc = accs[i]
        out.append(
            Filing(
                accession=acc,
                form=recent["form"][i],
                filing_date=recent["filingDate"][i],
                items=recent.get("items", [""] * len(accs))[i] or "",
                primary_doc_url=_ARCHIVE_URL.format(
                    cik=cik, acc=acc.replace("-", ""), doc=recent["primaryDocument"][i]
                ),
            )
        )
        if len(out) >= limit:
            break
    return out


# SEC 8-K item 코드 → 한글 요약(리스트 표시용, LLM 없이). 주요 항목만; 미매핑은 코드 그대로.
FORM_8K_ITEMS = {
    "1.01": "주요계약 체결", "1.02": "주요계약 종료", "2.01": "자산 취득·처분",
    "2.02": "실적 발표", "2.03": "채무 발생", "3.01": "상장폐지 통보",
    "4.01": "회계법인 변경", "4.02": "재무제표 신뢰불가", "5.01": "지배구조 변경",
    "5.02": "임원 변동", "5.03": "정관 변경", "5.07": "주주총회 결과",
    "7.01": "Reg FD 공시", "8.01": "기타 주요사건", "9.01": "재무제표·첨부",
}


def describe_8k_items(items: str) -> str:
    """8-K item 코드 문자열('5.02,7.01') → '임원 변동 · Reg FD 공시'. 미매핑 코드는 그대로."""
    codes = [c.strip() for c in items.split(",") if c.strip()]
    labels = [FORM_8K_ITEMS.get(c, f"항목 {c}") for c in codes]
    return " · ".join(labels) if labels else "8-K 공시"
