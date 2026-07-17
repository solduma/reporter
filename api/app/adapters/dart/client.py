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

from app.adapters.dart import throttle as dart_throttle
from app.domain.disclosure import Disclosure, OwnershipChange  # 하위호환 재노출(정의는 domain)

logger = logging.getLogger(__name__)


class DartQuotaExceeded(Exception):
    """DART 일일 조회 한도(status 020) 초과. 재시도로 풀리지 않으므로(한도는 자정 리셋)
    호출측은 데이터없음(013)과 달리 '없음'으로 오인 말고 중단·대기해야 한다."""


def _raise_if_quota(data: dict) -> None:
    """DART 응답 status 가 020(한도초과)이면 예외. 013(데이터없음) 등과 구분하기 위함."""
    if data.get("status") == "020":
        raise DartQuotaExceeded(data.get("message") or "DART 사용한도 초과")


def configure_from_settings(settings) -> None:
    """Settings 의 dart_api_key(+backup)로 throttle 키 링을 설정한다(020 시 자동 폴오버).

    링을 primary 부터 재시작하므로 배치·요청 진입 시 호출하면 자정 한도 회복이 반영된다."""
    dart_throttle.configure_keys(settings.dart_api_key, settings.dart_api_key_backup)


_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
_ELESTOCK_URL = "https://opendart.fss.or.kr/api/elestock.json"
_STOCK_TOTQY_URL = "https://opendart.fss.or.kr/api/stockTotqySttus.json"  # DS002 주식총수현황
_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 분기 → DART 보고서 코드. 1Q=11013·반기=11012·3Q=11014·사업보고서(연간)=11011.
DART_REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


def _amount(row: dict) -> float | None:
    """DART 금액 문자열('1,234' / '-' / '') → float(원). 파싱 불가면 None."""
    raw = (row.get("thstrm_amount") or "").replace(",", "").strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_field(row: dict, key: str) -> int | None:
    """DART 정수 필드('1,234' / '-' / '') → int. 파싱 불가면 None."""
    raw = (row.get(key) or "").replace(",", "").strip()
    if not raw or raw == "-":
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


@dataclass
class StockTotal:
    """주식총수 현황(DS002). 발행주식총수·자기주식·유통주식(보통주 기준)."""

    issued: int | None = None  # 발행주식의 총수(istc_totqy)
    treasury: int | None = None  # 자기주식수(tesstk_co)
    outstanding: int | None = None  # 유통주식수(distb_stock_co = 발행-자기)


def fetch_stock_total(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> StockTotal | None:
    """DS002 주식의 총수 현황 → 보통주 발행/자기/유통 주식수. 실패·데이터없음이면 None.

    se(구분)에 '보통주'가 있으면 그 행, 없으면 '합계' 행을 쓴다(우선주 분리 공시 대비).
    KRX fetch_shares(대부분 결측) 대체 — EV/EBITDA·PER 시총 계산의 주식수 앵커.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    }
    try:
        resp = dart_throttle.get(session, _STOCK_TOTQY_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart stock_total failed %s %sQ%s: %s", corp_code, year, quarter, e)
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    rows = data.get("list", [])
    common = next((r for r in rows if "보통주" in (r.get("se") or "")), None)
    total = next((r for r in rows if "합계" in (r.get("se") or "")), None)
    row = common or total or (rows[0] if rows else None)
    if row is None:
        return None
    return StockTotal(
        issued=_int_field(row, "istc_totqy"),
        treasury=_int_field(row, "tesstk_co"),
        outstanding=_int_field(row, "distb_stock_co"),
    )


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
    borrowings: float | None = None  # 총차입(단기·장기·사채, BS 시점값) — EV 순차입용
    cash: float | None = None  # 현금및현금성자산(BS 시점값)

    @property
    def net_debt(self) -> float | None:
        """순차입 = 총차입 - 현금. 둘 다 없으면 None(EV 산출 시 순차입 0 취급 대신 미반영)."""
        if self.borrowings is None and self.cash is None:
            return None
        return (self.borrowings or 0.0) - (self.cash or 0.0)


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
            resp = dart_throttle.get(session, _FNLTT_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("dart income failed %s %sQ%s: %s", corp_code, year, quarter, e)
            return None
        _raise_if_quota(data)
        if data.get("status") != "000":
            continue  # 013(데이터없음) → 다음 fs_div
        return _parse_income_equity(data.get("list", []))
    return None


def _parse_income_equity(rows: list[dict]) -> IncomeEquity:
    """손익·자본은 account_id(안정), 차입금·현금은 계정명(표준태그 불안정)으로 함께 뽑는다."""
    fin = IncomeEquity()
    borrowings = 0.0
    got_borrowing = False
    for row in rows:
        aid = row.get("account_id") or ""
        nm = (row.get("account_nm") or "").replace(" ", "")
        sj = row.get("sj_div")
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
        # 순차입용: 차입금 합산·현금(BS 계정명). 누계·잔액 아님.
        elif sj == "BS" and nm == "현금및현금성자산" and fin.cash is None:
            fin.cash = amt
        elif sj == "BS" and any(
            k in nm for k in ("단기차입금", "장기차입금", "사채", "유동성장기부채")
        ) and "누계" not in nm:
            borrowings += amt
            got_borrowing = True
    if got_borrowing:
        fin.borrowings = borrowings
    return fin


@dataclass
class CorpMapping:
    stock_code: str
    corp_code: str
    corp_name: str


def fetch_corp_mappings(api_key: str, session: requests.Session) -> list[CorpMapping]:
    """corpCode.xml(zip) 을 받아 상장사(stock_code 보유) 매핑만 반환한다."""
    try:
        resp = dart_throttle.get(session, _CORPCODE_URL, params={"crtfc_key": api_key}, timeout=30)
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
            resp = dart_throttle.get(session, _LIST_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("dart list failed %s: %s", corp_code, e)
            break

        _raise_if_quota(data)
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


# 정기공시 종류 → (report_nm 키워드, 회계연도 종료월). '분기보고서'는 1Q·3Q 둘 다라 report_nm
# 의 대상기간(YYYY.03)으로 1Q 를 특정한다.
_REPORT_KEYWORDS = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}
_REPORT_PERIOD_MONTH = {"annual": "12", "half": "06", "quarter": "03"}


def find_periodic_report(
    api_key: str, corp_code: str, year: int, kind: str, session: requests.Session
) -> str | None:
    """해당 회계연도 정기공시(kind=annual|half|quarter)의 접수번호. 없으면 None.

    제출 시점이 종류마다 다르다: 사업보고서는 다음 해 3월, 반기/분기는 당해 회계연도 내
    (반기 ~8월·분기 ~5/11월). 따라서 조회 창을 종류별로 다르게 잡는다. '분기보고서'는 1Q·3Q
    둘 다 매칭되므로 report_nm 의 대상기간(YYYY.03)으로 1Q 만 고른다. 정정 제출이 있으면
    최신(가장 늦은 접수)을 택해 확정 재무를 쓴다.
    """
    keyword = _REPORT_KEYWORDS.get(kind)
    if not keyword:
        return None
    # annual 은 다음 해 상반기 제출, half/quarter 는 당해 연중 제출.
    begin, end = (f"{year + 1}0101", f"{year + 1}0930") if kind == "annual" else (f"{year}0301", f"{year + 1}0331")
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": begin,
        "end_de": end,
        "pblntf_ty": "A",  # 정기공시
        "page_count": 100,
    }
    try:
        resp = dart_throttle.get(session, _LIST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart periodic list failed %s %s: %s", corp_code, year, e)
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    # 대상 회계연도·기간이 report_nm 에 'YYYY.MM' 로 명시된다(예: '분기보고서 (2026.03)').
    tag = f"{year}.{_REPORT_PERIOD_MONTH[kind]}"
    matches = [
        r for r in data.get("list", [])
        if keyword in (r.get("report_nm") or "") and tag in (r.get("report_nm") or "")
    ]
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
        resp = dart_throttle.get(
            session, _DOCUMENT_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=30
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


def _to_int(raw: str | None) -> int:
    """'3,000'·'-1,700'·'' → int. 콤마·공백 제거, 파싱 실패 시 0."""
    if not raw:
        return 0
    try:
        return int(re.sub(r"[,\s]", "", raw))
    except ValueError:
        return 0


# 소유변동 표의 사유는 '<사유>(+)' 또는 '<사유>(-)' 로 적힌다(예 '장내매수(+)', '증여(-)').
# 표 안내문의 '매매'·'취득/처분' 같은 라벨이 아닌, 실제 변동행의 사유 토큰만 잡는다.
_OWNERSHIP_REASON = re.compile(r"([가-힣]{2,10})\s*\(\s*([+\-])\s*\)")
_REASON_LABELS = {"취득", "처분", "취득처분", "매매"}  # 표 헤더·안내문 라벨(사유 아님)
# 세부변동내역 표는 헤더 마지막 컬럼 '변동후' 뒤에 사유행이 온다. 이 앞의 부호 범례
# (예 '증감수량의 (+)는 취득...')를 사유로 오인하지 않도록 '변동후' 이후 구간만 훑는다.
_OWNERSHIP_TABLE_ANCHOR = "변동후"


def extract_ownership_reason(document_text: str) -> str:
    """소유상황보고서 원문 텍스트에서 변동사유(장내매수/장내매도/증여 등)를 추출한다.

    세부변동 표 헤더('변동후') 뒤 구간에서 첫 실제 사유 토큰을 반환한다(앞의 부호 범례 회피).
    앵커가 없으면 전체를 훑는다. 표 헤더 라벨('취득/처분')은 제외한다. 없으면 빈 문자열.
    """
    anchor = document_text.rfind(_OWNERSHIP_TABLE_ANCHOR)
    region = document_text[anchor:] if anchor != -1 else document_text
    for token, _sign in _OWNERSHIP_REASON.findall(region):
        if token not in _REASON_LABELS:
            return token
    return ""


def fetch_ownership_changes(
    api_key: str, corp_code: str, session: requests.Session
) -> dict[str, OwnershipChange]:
    """corp_code 의 임원·주요주주 소유보고(elestock.json) → {rcept_no: 소유변동}.

    구조화 API 라 부호있는 증감(sp_stock_lmp_irds_cnt)·수량·직위를 그대로 준다. 태그 제거로
    뭉개지는 문서 표와 달리 방향이 명확하다. 실패·데이터없음이면 빈 dict.
    """
    try:
        resp = dart_throttle.get(
            session, _ELESTOCK_URL, params={"crtfc_key": api_key, "corp_code": corp_code}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart elestock failed %s: %s", corp_code, e)
        return {}
    _raise_if_quota(data)
    if data.get("status") != "000":  # 013=데이터없음 등
        return {}
    changes: dict[str, OwnershipChange] = {}
    for row in data.get("list", []):
        rcept_no = row.get("rcept_no", "")
        if not rcept_no:
            continue
        changes[rcept_no] = OwnershipChange(
            reporter=(row.get("repror") or "").strip(),
            position=(row.get("isu_exctv_ofcps") or "").replace("\n", " ").strip(),
            is_registered=(row.get("isu_exctv_rgist_at") or "").strip(),
            shares_after=_to_int(row.get("sp_stock_lmp_cnt")),
            shares_delta=_to_int(row.get("sp_stock_lmp_irds_cnt")),
        )
    return changes
