"""DART OpenAPI — corpCode 매핑 적재 + 공시 목록 조회.

- corpCode.xml(zip): 전체 기업의 stock_code↔corp_code 매핑. 주기적으로 적재.
- list.json: corp_code + 기간으로 공시 목록. 공시는 corp_code 기준 조회다.
DART_API_KEY(crtfc_key) 필요. 무료·일 2만건.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
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
    # 유형(감가상각비)·무형(무형자산상각비) 상각을 각각 **최대값**으로 잡는다. 합산하면
    # 요약 라인 + 항목별 세부 라인(유형자산감가상각비 등)이 겹쳐 이중계상되므로, 카테고리별로
    # 가장 큰 한 값(=요약 또는 총액)만 취해 이중계상을 막는다.
    dep_tangible = 0.0
    dep_intangible = 0.0
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
        elif sj == "CF" and "무형자산상각비" in nm and "대손" not in nm:
            dep_intangible = max(dep_intangible, abs(amt))
            got_dep = True
        elif sj == "CF" and "감가상각비" in nm and "무형" not in nm and "대손" not in nm:
            dep_tangible = max(dep_tangible, abs(amt))
            got_dep = True
        elif sj == "BS" and nm == "현금및현금성자산" and st.cash is None:
            st.cash = amt
        elif sj == "BS" and any(
            k in nm for k in ("단기차입금", "장기차입금", "사채", "유동성장기부채")
        ) and "누계" not in nm:
            borrowings += amt
            got_borrowing = True
    if got_dep:
        st.depreciation = dep_tangible + dep_intangible
    if got_borrowing:
        st.borrowings = borrowings
    return st


@dataclass
class IncomeEquity:
    """한 종목·기간의 손익·자본 계정(원 단위). PER/PBR/PSR 역산용.

    revenue·net_income 은 회계연도 **누적(YTD)**, equity·eps 는 시점/기간 값이다.
    """

    revenue: float | None = None  # 매출(영업수익), 누적
    net_income: float | None = None  # 지배주주 순이익, 누적
    eps: float | None = None  # 기본주당이익(원), 누적
    equity: float | None = None  # 지배주주 자본총계(BS 시점값)
    operating_income: float | None = None  # 영업이익(EBITDA 산출용), 누적


# IFRS 표준 account_id 로 매칭한다(계정명은 회사마다 편차가 커 신뢰 불가).
# 과거(≤2018경) 공시는 구 태그(ifrs_*, 언더스코어), 최근은 ifrs-full_* (하이픈)을 쓴다 — 둘 다 본다.
_AID_REVENUE = {"ifrs-full_Revenue", "ifrs_Revenue"}
_AID_OP = {"dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"}
_AID_NI_OWNERS = {
    "ifrs-full_ProfitLossAttributableToOwnersOfParent",
    "ifrs_ProfitLossAttributableToOwnersOfParent",
}
_AID_NI = {"ifrs-full_ProfitLoss", "ifrs_ProfitLoss"}  # 지배주주 항목 없을 때 폴백
_AID_EPS = {"ifrs-full_BasicEarningsLossPerShare", "ifrs_BasicEarningsLossPerShare"}
_AID_EQ_OWNERS = {
    "ifrs-full_EquityAttributableToOwnersOfParent",
    "ifrs_EquityAttributableToOwnersOfParent",
}
_AID_EQ = {"ifrs-full_Equity", "ifrs_Equity"}  # 지배주주 지분 없을 때 폴백


def fetch_income_and_equity(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> IncomeEquity | None:
    """DART 전체재무제표에서 매출·지배순이익·EPS·지배자본을 account_id 로 추출한다.

    연결(CFS) 우선, 없으면 별도(OFS). 손익은 IS/CIS 어디에나 올 수 있어 sj_div 무관하게
    account_id 로 잡는다. 실패·데이터없음이면 None.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
    for fs_div in ("CFS", "OFS"):
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
            logger.warning("dart income failed %s %sQ%s: %s", corp_code, year, quarter, e)
            return None
        if data.get("status") != "000":
            continue  # 013(데이터없음) → 다음 fs_div
        return _parse_income_equity(data.get("list", []))
    return None


def _parse_income_equity(rows: list[dict]) -> IncomeEquity:
    fin = IncomeEquity()
    for row in rows:
        aid = row.get("account_id") or ""
        amt = _amount(row)
        if amt is None:
            continue
        # 지배주주 항목을 우선하되(덮어쓰기), 없으면 전체 항목으로 채운다(setdefault 성격).
        if aid in _AID_REVENUE and fin.revenue is None:
            fin.revenue = amt
        elif aid in _AID_OP and fin.operating_income is None:
            fin.operating_income = amt
        elif aid in _AID_NI_OWNERS:
            fin.net_income = amt  # 지배주주 우선(덮어씀)
        elif aid in _AID_NI and fin.net_income is None:
            fin.net_income = amt
        elif aid in _AID_EPS and fin.eps is None:
            fin.eps = amt
        elif aid in _AID_EQ_OWNERS:
            fin.equity = amt  # 지배주주 우선
        elif aid in _AID_EQ and fin.equity is None:
            fin.equity = amt
    return fin


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


# 정기공시 보고서 종류 → report_nm 키워드. 정정공시(재무 최신)를 위해 최신 접수건을 택한다.
_REPORT_KEYWORDS = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}


def find_periodic_report(
    api_key: str, corp_code: str, year: int, kind: str, session: requests.Session
) -> str | None:
    """해당 연도 정기공시(kind=annual|half|quarter)의 접수번호. 없으면 None.

    보고서는 회계연도 종료 후 다음 해에 제출되므로 [year+1.01 ~ year+1.09]에서 찾는다.
    정정 제출이 있으면 최신(가장 늦은 접수)을 택해 확정 재무를 쓴다. 분기는 1Q·3Q 둘 다
    '분기보고서'라 여기선 최초 1건만 — 분기 상세 백필은 호출측이 접수일로 구분.
    """
    keyword = _REPORT_KEYWORDS.get(kind)
    if not keyword:
        return None
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": f"{year + 1}0101",
        "end_de": f"{year + 1}0930",
        "pblntf_ty": "A",  # 정기공시
        "page_count": 100,
    }
    try:
        resp = session.get(_LIST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart periodic list failed %s %s: %s", corp_code, year, e)
        return None
    if data.get("status") != "000":
        return None
    matches = [r for r in data.get("list", []) if keyword in (r.get("report_nm") or "")]
    if not matches:
        return None
    # 접수일 최신순(정정 반영). rcept_no 는 시간순 증가라 최대값이 최신.
    return max(matches, key=lambda r: r.get("rcept_no", "")).get("rcept_no")


# 공시 본문 XML 의 태그를 제거해 순수 텍스트로. 표·서식은 버리고 판단에 쓸 서술만 남긴다.
def _strip_document_xml(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", text)  # 태그 제거
    text = re.sub(r"&[a-zA-Z]+;", " ", text)  # 잔여 엔티티
    return re.sub(r"\s+", " ", text).strip()


def fetch_document_text(
    api_key: str, rcept_no: str, session: requests.Session, max_chars: int = 6000
) -> str:
    """공시 원문(document.xml, zip 내 XML)을 받아 태그를 벗겨 앞 max_chars 만 반환한다.

    첨부가 여러 XML 이면 이어붙인다. 실패·빈 응답이면 빈 문자열(호출측은 제목-only 로 폴백).
    """
    try:
        resp = session.get(
            _DOCUMENT_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=30
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("dart document fetch failed %s: %s", rcept_no, e)
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            parts = [_strip_document_xml(zf.read(n)) for n in zf.namelist()]
    except (zipfile.BadZipFile, KeyError) as e:
        logger.warning("dart document parse failed %s: %s", rcept_no, e)
        return ""
    return " ".join(p for p in parts if p)[:max_chars]
