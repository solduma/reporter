"""DART OpenAPI — corpCode 매핑 적재 + 공시 목록 조회.

- corpCode.xml(zip): 전체 기업의 stock_code↔corp_code 매핑. 주기적으로 적재.
- list.json: corp_code + 기간으로 공시 목록. 공시는 corp_code 기준 조회다.
DART_API_KEY(crtfc_key) 필요. 무료·일 2만건.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 분기 → DART 보고서 코드. 1Q=11013·반기=11012·3Q=11014·사업보고서(연간)=11011.
DART_REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


@dataclass
class FinStatement:
    """한 종목·기간의 재무제표에서 EV/EBITDA 산출에 필요한 계정 값(원 단위)."""

    operating_income: float | None = None  # 영업이익(IS)
    depreciation: float | None = None  # 감가상각비+무형자산상각비(CF)
    borrowings: float | None = None  # 단기·장기차입금+사채 합(BS)
    cash: float | None = None  # 현금및현금성자산(BS)

    @property
    def ebitda(self) -> float | None:
        if self.operating_income is None:
            return None
        return self.operating_income + (self.depreciation or 0.0)

    @property
    def net_debt(self) -> float | None:
        if self.borrowings is None and self.cash is None:
            return None
        return (self.borrowings or 0.0) - (self.cash or 0.0)


def _amount(row: dict) -> float | None:
    """DART 금액 문자열('1,234' / '-' / '') → float(원). 파싱 불가면 None."""
    raw = (row.get("thstrm_amount") or "").replace(",", "").strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def fetch_financial_statement(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> FinStatement | None:
    """DART 전체 재무제표(fnlttSinglAcntAll)에서 EBITDA·순차입금 계정을 추출한다.

    연결(CFS) 우선, 없으면 별도(OFS). 계정명은 회사마다 편차가 있어 부분일치로 매칭한다.
    실패·데이터없음이면 None.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
    for fs_div in ("CFS", "OFS"):  # 연결 우선
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        try:
            resp = session.get(_FNLTT_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("dart fnltt failed %s %sQ%s: %s", corp_code, year, quarter, e)
            return None
        if data.get("status") != "000":
            continue  # 013(데이터없음) → 다음 fs_div 시도
        return _parse_statement(data.get("list", []))
    return None


def _parse_statement(rows: list[dict]) -> FinStatement:
    """계정 리스트에서 영업이익·감가상각비·차입금·현금을 계정명 부분일치로 합산한다.

    감가상각은 **현금흐름표(CF)의 가산 조정 항목**만 센다(BS '감가상각누계액'은 잔액이라 제외).
    영업이익은 IS/CIS 어디에 있을 수 있어 둘 다 본다. 대손상각비는 D&A 가 아니라 제외.
    """
    st = FinStatement()
    borrowings = 0.0
    got_borrowing = False
    depreciation = 0.0
    got_dep = False
    for row in rows:
        sj = row.get("sj_div")  # BS/IS/CIS/CF
        nm = (row.get("account_nm") or "").replace(" ", "")
        amt = _amount(row)
        if amt is None:
            continue
        # 계정명이 '영업이익' 또는 '영업이익(손실)' 등으로 나와 접두 일치로 잡는다.
        if sj in ("IS", "CIS") and st.operating_income is None and nm.startswith("영업이익"):
            st.operating_income = amt
        elif sj == "CF" and ("감가상각비" in nm or "무형자산상각비" in nm) and "대손" not in nm:
            depreciation += abs(amt)  # 조정 항목은 부호가 섞일 수 있어 절대값 가산
            got_dep = True
        elif sj == "BS" and nm == "현금및현금성자산" and st.cash is None:
            st.cash = amt
        elif sj == "BS" and any(
            k in nm for k in ("단기차입금", "장기차입금", "사채", "유동성장기부채")
        ) and "누계" not in nm:
            borrowings += amt
            got_borrowing = True
    if got_dep:
        st.depreciation = depreciation
    if got_borrowing:
        st.borrowings = borrowings
    return st


@dataclass
class CorpMapping:
    stock_code: str
    corp_code: str
    corp_name: str


@dataclass
class Disclosure:
    rcept_no: str
    corp_code: str
    stock_code: str
    report_nm: str
    flr_nm: str
    rcept_dt: date
    dart_url: str


def fetch_corp_mappings(api_key: str, session: requests.Session) -> list[CorpMapping]:
    """corpCode.xml(zip) 을 받아 상장사(stock_code 보유) 매핑만 반환한다."""
    try:
        resp = session.get(_CORPCODE_URL, params={"crtfc_key": api_key}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("corpCode fetch failed: %s", e)
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        root = ElementTree.fromstring(xml_bytes)
    except (zipfile.BadZipFile, ElementTree.ParseError, IndexError) as e:
        logger.warning("corpCode parse failed: %s", e)
        return []

    mappings: list[CorpMapping] = []
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:  # 상장사만
            mappings.append(
                CorpMapping(stock_code, corp_code, (item.findtext("corp_name") or "").strip())
            )
    return mappings


def fetch_disclosures(
    api_key: str,
    corp_code: str,
    stock_code: str,
    begin: date,
    end: date,
    session: requests.Session,
) -> list[Disclosure]:
    """corp_code + 기간으로 공시 목록을 조회한다(페이지네이션 처리)."""
    disclosures: list[Disclosure] = []
    page = 1
    while page <= 20:  # 안전 상한
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": page,
            "page_count": 100,
        }
        try:
            resp = session.get(_LIST_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("dart list failed %s: %s", corp_code, e)
            break

        if data.get("status") != "000":  # 013=데이터없음 등
            break

        for row in data.get("list", []):
            rcept_no = row.get("rcept_no", "")
            try:
                rcept_dt = datetime.strptime(row["rcept_dt"], "%Y%m%d").date()
            except (KeyError, ValueError):
                continue
            disclosures.append(
                Disclosure(
                    rcept_no=rcept_no,
                    corp_code=corp_code,
                    stock_code=stock_code,
                    report_nm=(row.get("report_nm") or "").strip(),
                    flr_nm=(row.get("flr_nm") or "").strip(),
                    rcept_dt=rcept_dt,
                    dart_url=_DART_VIEWER.format(rcept_no=rcept_no),
                )
            )

        if page >= data.get("total_page", 1):
            break
        page += 1

    return disclosures
