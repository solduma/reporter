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
from app.adapters.dart.report_parser import fetch_report_zip
from app.adapters.financial_ontology import get_ontology_port
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
# 단일 재무제표(fnlttSinglAcnt) — fnlttSinglAcntAll 이 013(데이터없음)인 CFS 기간의 요약 폴백.
# 응답에 CFS(첫 번째)·OFS(두 번째) 재무제표가 모두 포함되고 fs_div 파라미터는 무시된다.
_FNLTT_SINGL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
_ELESTOCK_URL = "https://opendart.fss.or.kr/api/elestock.json"
_STOCK_TOTQY_URL = "https://opendart.fss.or.kr/api/stockTotqySttus.json"  # DS002 주식총수현황
_ALOTMATTER_URL = "https://opendart.fss.or.kr/api/alotMatter.json"  # DS002 배당에관한사항
_FNLTT_INDX_URL = (
    "https://opendart.fss.or.kr/api/fnlttSinglIndx.json"  # DS003 단일회사 주요 재무지표
)
_HYSLR_URL = "https://opendart.fss.or.kr/api/hyslrSttus.json"  # DS005 최대주주 현황
_OTR_CPR_URL = "https://opendart.fss.or.kr/api/otrCprInvstmntSttus.json"  # DS002 타법인 출자현황
_MAJORSTOCK_URL = "https://opendart.fss.or.kr/api/majorstock.json"  # DS004 대량보유 상황보고
_CVBD_ISSUE_URL = "https://opendart.fss.or.kr/api/cvbdIsDecsn.json"  # DS005 전환사채권 발행결정
_BDWT_ISSUE_URL = (
    "https://opendart.fss.or.kr/api/bdwtIsDecsn.json"  # DS005 신주인수권부사채권 발행결정
)
# 부문별 매출(iotHom3MdQe) — 사업보고서 원문의 유일한 구조화 DART 소스. 제품/지역/부문 매출 비중.
_SEGMENT_SALES_URL = "https://opendart.fss.or.kr/api/iotHom3MdQe.json"
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


def _float_field(row: dict, key: str) -> float | None:
    """DART 실수 필드('1,446' / '2.70' / '-' / '') → float. 파싱 불가면 None."""
    raw = (row.get(key) or "").replace(",", "").strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Dividend:
    """배당에관한사항(DS002 alotMatter). 보통주 주당현금배당금·현금배당수익률(당기)."""

    dps: float | None = None  # 주당 현금배당금(원)
    div_yield: float | None = None  # 현금배당수익률(%)


def fetch_dividend(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> Dividend | None:
    """DS002 배당에관한사항 → 보통주 주당현금배당금·현금배당수익률(당기 thstrm). 실패·없음이면 None.

    alotMatter 는 se(항목명)·stock_knd(주식종류)로 행을 구분한다. '주당 현금배당금(원)'·
    '현금배당수익률(%)'의 보통주 행에서 thstrm(당기)을 쓴다(se 는 공백 편차가 있어 제거 후 매칭).
    네이버 스크랩 dps/div_yield 대체 — 정기보고서 유래로 소스 통일.
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
        resp = dart_throttle.get(session, _ALOTMATTER_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart dividend failed %s %sQ%s: %s", corp_code, year, quarter, e)
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    dps = _dividend_row(data["list"], "주당현금배당금")
    div_yield = _dividend_row(data["list"], "현금배당수익률")
    if dps is None and div_yield is None:
        return None
    return Dividend(dps=dps, div_yield=div_yield)


def _dividend_row(rows: list[dict], se_key: str) -> float | None:
    """alotMatter 행에서 se(공백제거)에 se_key 가 있는 보통주 행의 당기(thstrm) 값을 뽑는다.

    stock_knd 가 '보통주'인 행 우선(우선주 분리 공시 대비), 없으면 '-'(주식종류 무관) 행.
    '주식배당'·'배당성향' 등 유사 항목과 섞이지 않도록 호출측이 정확한 se_key 를 넘긴다.
    """
    matched = [r for r in rows if se_key in (r.get("se") or "").replace(" ", "")]
    common = next((r for r in matched if "보통주" in (r.get("stock_knd") or "")), None)
    row = common or next((r for r in matched if (r.get("stock_knd") or "").strip() == "-"), None)
    return _float_field(row, "thstrm") if row else None


# DS003 재무지표 분류: 수익성/안정성/성장성/활동성. ROE 는 수익성(M210000)에 있다.
_IDX_PROFITABILITY = "M210000"


def fetch_roe(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> float | None:
    """DS003 단일회사 주요재무지표(수익성)에서 ROE(%)를 뽑는다. 실패·없음이면 None.

    fnlttSinglIndx 는 idx_cl_code(분류)별로 idx_nm(지표명)·idx_val(값) 행을 준다. 수익성지표
    (M210000)의 'ROE' 행 값을 쓴다. **2023 3Q부터 제공** — 그 이전은 status 013 이라 None 이
    돌아가고 호출측이 네이버 스크랩으로 폴백한다. 네이버 ROE 스크랩 대체(소스 통일).
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "idx_cl_code": _IDX_PROFITABILITY,
    }
    try:
        resp = dart_throttle.get(session, _FNLTT_INDX_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart roe failed %s %sQ%s: %s", corp_code, year, quarter, e)
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    # idx_nm 은 'ROE' 정확히. 유사어(자기자본영업이익률 등)와 섞이지 않게 완전일치로 잡는다.
    row = next((r for r in data["list"] if (r.get("idx_nm") or "").strip() == "ROE"), None)
    return _float_field(row, "idx_val") if row else None


@dataclass
class HyslrRow:
    """최대주주현황(hyslrSttus) 개별 행 — 주주명/관계/지분율/법인여부.

    기업분석 화면 지분구조 좌측(주주 명부) 원천. fetch_largest_shareholders(집계)·
    fetch_related_companies(parent 파생)·ingest(Shareholder upsert)가 공유해 hyslrSttus
    중복 호출을 피한다.
    """

    name: str  # 주주명(법인/개인)
    relate: str  # 최대주주 본인/배우자/자녀/최대주주의 특수관계인 ...
    stake_pct: float | None  # 기말 지분율(%)
    is_corporate: bool  # 법인 접미사로 판정


@dataclass
class LargestShareholders:
    """최대주주 현황(DS005 hyslrSttus). 최대주주명·최대주주+특수관계인 합산 지분율(기말)."""

    top_holder: str | None = None  # 최대주주 본인 이름
    group_stake_pct: float | None = None  # 최대주주+특수관계인 합산 지분율(%)


def fetch_hyslr_rows(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> list[HyslrRow] | None:
    """DS005 최대주주현황 → 개별 주주 행(이름/관계/지분율/법인여부). 실패·데이터없음이면 None.

    개인·법인이 보통주/우선주 등 여러 행으로 쪼개진 원시 list 를 정제한다. 소계·합계 행
    (_is_total_row)은 제외. 집계(fetch_largest_shareholders)·parent 파생(fetch_related_companies)·
    Shareholder upsert(ingest)가 이 결과를 공유해 hyslrSttus HTTP 호출을 1회로 유지한다.
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
        resp = dart_throttle.get(session, _HYSLR_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart shareholders failed %s %sQ%s: %s", corp_code, year, quarter, e)
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    rows: list[HyslrRow] = []
    for r in data.get("list", []):
        nm = (r.get("nm") or "").strip()
        if not nm or _is_total_row(nm):
            continue
        rows.append(
            HyslrRow(
                name=nm,
                relate=(r.get("relate") or "").strip(),
                stake_pct=_float_field(r, "trmend_posesn_stock_qota_rt"),
                is_corporate=_looks_corporate(nm),
            )
        )
    return rows


def fetch_largest_shareholders(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> LargestShareholders | None:
    """DS005 최대주주 현황 → 최대주주명 + 특수관계인 합산 지분율(기말). 실패·없음이면 None.

    개인·법인이 보통주/우선주 등 여러 행으로 쪼개져 나오므로 기말 지분율을 전 행 합산해 지배지분을
    근사한다. 최대주주명은 relate='최대주주 본인' 행의 nm. 딥다이브 overview 의 LLM 자유서술 대신
    구조화 지분을 주입한다. fetch_hyslr_rows 로 파싱한 행을 집계한다.
    """
    rows = fetch_hyslr_rows(api_key, corp_code, year, quarter, session)
    if rows is None:
        return None
    total_pct = sum(r.stake_pct or 0.0 for r in rows)
    top = next((r for r in rows if "최대주주 본인" in r.relate), rows[0] if rows else None)
    if top is None or total_pct <= 0:
        return None
    return LargestShareholders(
        top_holder=top.name or None,
        group_stake_pct=round(total_pct, 2),
    )


@dataclass
class RelatedParty:
    """관계사 1건 — 웹서치 관련성 판정 alias 원천.

    otrCprInvstmntSttus 의 추가 필드(inv_purpose·book_value·sub_total_assets·sub_net_profit)는
    자회사 필터(이익 10%+/적자/출자목적)에 사용. None 이면 DART 응답에 해당 필드가 없거나
    hyslrSttus(모회사)에서 파생된 행.
    """

    name: str  # 법인명(관계사)
    relation: str  # 'parent'(모회사/지배주주) | 'subsidiary'(50%+) | 'investor'(그 외 출자)
    stake_pct: float | None = None
    inv_purpose: str | None = None  # 출자목적(otrCpr 전용)
    book_value: int | None = None  # 기말 장부가액(원, otrCpr 전용)
    sub_total_assets: int | None = None  # 자회사 총자산(원, otrCpr 전용)
    sub_net_profit: int | None = None  # 자회사 당기순이익(원, otrCpr 전용)


# 개인(법인 아님) 최대주주를 걸러내는 법인 접미사/키워드. 이 중 하나라도 있으면 법인으로 본다.
_CORP_MARKERS = ("주식회사", "㈜", "(주)", "유한", "홀딩스", "그룹", "Inc", "Corp", "Ltd", "Co.")
# 소계·합계 행(관계사 아님) — DART 응답 마지막에 붙는 집계 행 제외.
_TOTAL_MARKERS = ("계", "합계", "소계")


def _looks_corporate(name: str) -> bool:
    return any(m in name for m in _CORP_MARKERS)


def _is_total_row(name: str) -> bool:
    return name.strip() in _TOTAL_MARKERS


def fetch_related_companies(
    api_key: str,
    corp_code: str,
    year: int,
    quarter: int,
    session: requests.Session,
    *,
    hyslr_rows: list[HyslrRow] | None = None,
) -> list[RelatedParty]:
    """모회사(hyslrSttus 법인 최대주주) + 자회사·출자사(otrCprInvstmntSttus)를 관계사 목록으로.

    - 모회사: 최대주주현황에서 relate='최대주주 본인'이며 법인으로 보이는 nm(개인 지배주주 제외).
    - 자회사/출자사: 타법인출자현황의 inv_prm. 기말지분율 50%+ 는 subsidiary, 그 외는 investor.
    실패·없음이면 빈 리스트(부분 실패 허용 — 한 소스 실패가 다른 소스를 막지 않는다).

    hyslr_rows 를 전달하면 모회사 파생에 재사용해 hyslrSttus 중복 호출을 피한다(ingest 가
    fetch_hyslr_rows 결과로 Shareholder 도 같이 채울 때 사용). None 이면 내부에서 직접 호출.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return []
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    }
    out: list[RelatedParty] = []

    # 모회사(지배주주 법인) — 최대주주현황. hyslr_rows 가 있으면 재사용(중복 HTTP 회피).
    rows = (
        hyslr_rows
        if hyslr_rows is not None
        else fetch_hyslr_rows(api_key, corp_code, year, quarter, session) or []
    )
    for r in rows:
        # 최대주주 본인 행(relate=='최대주주 본인')이 법인이면 모회사.
        if r.is_corporate and "최대주주 본인" in r.relate:
            out.append(RelatedParty(r.name, "parent", r.stake_pct))
            break

    # 자회사/출자사 — 타법인 출자현황.
    try:
        resp = dart_throttle.get(session, _OTR_CPR_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _raise_if_quota(data)
        if data.get("status") == "000":
            for r in data.get("list", []):
                nm = (r.get("inv_prm") or "").strip()
                if not nm or _is_total_row(nm):  # 소계·합계 행 제외
                    continue
                pct = _float_field(r, "trmend_blce_qota_rt")
                relation = "subsidiary" if (pct is not None and pct >= 50.0) else "investor"
                # otrCpr 추가 필드 — 자회사 필터(이익 10%+/적자/출자목적)용.
                inv_purpose = (r.get("invstmnt_purps") or "").strip() or None
                book_value = _int_field(r, "trmend_blce_acntbk_amount")
                sub_total_assets = _int_field(r, "recent_bsns_year_fnnr_sttus_tot_assets")
                sub_net_profit = _int_field(r, "recent_bsns_year_fnnr_sttus_thstrm_ntpf")
                out.append(
                    RelatedParty(
                        nm, relation, pct, inv_purpose, book_value, sub_total_assets, sub_net_profit
                    )
                )
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart related(investment) failed %s %sQ%s: %s", corp_code, year, quarter, e)

    return out


@dataclass
class MajorHolder:
    """대량보유 상황보고(majorstock.json) 1건 — 5%+ 주주."""

    rcept_dt: str  # 접수일자(YYYYMMDD)
    repror: str  # 대표보고자(주주명)
    stkqy: int | None = None  # 보유주식수
    stkrt: float | None = None  # 보유비율(%)
    stkqy_irds: int | None = None  # 증감
    stkrt_irds: float | None = None  # 증감(%)
    ctr_stkqy: int | None = None  # 주요체결 주식수(주식매매계약 체결, 이전 미완료 분)
    ctr_stkrt: float | None = None  # 주요체결 보유비율(%)
    report_resn: str = ""  # 보고사유


def fetch_major_shareholders(
    api_key: str, corp_code: str, session: requests.Session | None = None
) -> list[MajorHolder]:
    """DS004 대량보유 상황보고 → 5%+ 주주 목록(최신순). 실패·없음이면 빈 리스트."""
    params = {"crtfc_key": api_key, "corp_code": corp_code}
    s = session or requests.Session()
    try:
        resp = dart_throttle.get(s, _MAJORSTOCK_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _raise_if_quota(data)
        if data.get("status") != "000":
            return []
        out: list[MajorHolder] = []
        for r in data.get("list", []):
            out.append(
                MajorHolder(
                    rcept_dt=(r.get("rcept_dt") or "").strip(),
                    repror=(r.get("repror") or "").strip(),
                    stkqy=_int_field(r, "stkqy"),
                    stkrt=_float_field(r, "stkrt"),
                    stkqy_irds=_int_field(r, "stkqy_irds"),
                    stkrt_irds=_float_field(r, "stkrt_irds"),
                    ctr_stkqy=_int_field(r, "ctr_stkqy"),
                    ctr_stkrt=_float_field(r, "ctr_stkrt"),
                    report_resn=(r.get("report_resn") or "").strip(),
                )
            )
        return out
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart majorstock failed %s: %s", corp_code, e)
        return []


@dataclass
class CbIssue:
    """전환사채권 발행결정(cvbdIsDecsn.json) 1건."""

    rcept_no: str = ""
    bddd: str = ""  # 이사회결의일(YYYYMMDD)
    bd_fta: int | None = None  # 사채권면총액(원)
    cv_prc: int | None = None  # 전환가액(원/주)
    cvisstk_cnt: int | None = None  # 전환 발행 주식수
    cvisstk_tisstk_vs: float | None = None  # 주식총수 대비 비율(%)


def fetch_cb_issuance(
    api_key: str, corp_code: str, bgn_de: str, end_de: str, session: requests.Session | None = None
) -> list[CbIssue]:
    """DS005 전환사채권 발행결정 → CB 발행내역(최신순). 실패·없음이면 빈 리스트."""
    params = {"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de}
    s = session or requests.Session()
    try:
        resp = dart_throttle.get(s, _CVBD_ISSUE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _raise_if_quota(data)
        if data.get("status") != "000":
            return []
        out: list[CbIssue] = []
        for r in data.get("list", []):
            out.append(
                CbIssue(
                    rcept_no=(r.get("rcept_no") or "").strip(),
                    bddd=(r.get("bddd") or "").strip(),
                    bd_fta=_int_field(r, "bd_fta"),
                    cv_prc=_int_field(r, "cv_prc"),
                    cvisstk_cnt=_int_field(r, "cvisstk_cnt"),
                    cvisstk_tisstk_vs=_float_field(r, "cvisstk_tisstk_vs"),
                )
            )
        return out
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart cvbd failed %s: %s", corp_code, e)
        return []


@dataclass
class BwIssue:
    """신주인수권부사채권 발행결정(bdwtIsDecsn.json) 1건."""

    rcept_no: str = ""
    bddd: str = ""  # 이사회결의일(YYYYMMDD)
    bd_fta: int | None = None  # 사채권면총액(원)
    ex_prc: int | None = None  # 행사가액(원/주)
    nstk_isstk_cnt: int | None = None  # 행사 발행 주식수
    nstk_isstk_tisstk_vs: float | None = None  # 주식총수 대비 비율(%)


def fetch_bw_issuance(
    api_key: str, corp_code: str, bgn_de: str, end_de: str, session: requests.Session | None = None
) -> list[BwIssue]:
    """DS005 신주인수권부사채권 발행결정 → BW 발행내역(최신순). 실패·없음이면 빈 리스트."""
    params = {"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de}
    s = session or requests.Session()
    try:
        resp = dart_throttle.get(s, _BDWT_ISSUE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _raise_if_quota(data)
        if data.get("status") != "000":
            return []
        out: list[BwIssue] = []
        for r in data.get("list", []):
            out.append(
                BwIssue(
                    rcept_no=(r.get("rcept_no") or "").strip(),
                    bddd=(r.get("bddd") or "").strip(),
                    bd_fta=_int_field(r, "bd_fta"),
                    ex_prc=_int_field(r, "ex_prc"),
                    nstk_isstk_cnt=_int_field(r, "nstk_isstk_cnt"),
                    nstk_isstk_tisstk_vs=_float_field(r, "nstk_isstk_tisstk_vs"),
                )
            )
        return out
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart bdwt failed %s: %s", corp_code, e)
        return []


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
    capex: float | None = None  # 자본적지출(유형+무형자산 취득, CF 투자활동, 누적) — FCFF 산출용
    income_tax: float | None = None  # 법인세비용(누적) — 실효세율 분자
    pretax_income: float | None = None  # 법인세비용차감전순이익(누적) — 실효세율 분모
    interest_expense: float | None = None  # 이자비용(손익, 없으면 CF 이자지급 폴백) — 부채비용 분자

    @property
    def net_debt(self) -> float | None:
        """순차입 = 총차입 - 현금. 둘 다 없으면 None(EV 산출 시 순차입 0 취급 대신 미반영)."""
        if self.borrowings is None and self.cash is None:
            return None
        return (self.borrowings or 0.0) - (self.cash or 0.0)


def _dart_account_ids(*ontology_ids: str) -> set[str]:
    """ontology 정준 ID 에 매핑된 DART XBRL account_id 집합을 반환한다.

    financial-ontology 의 dart 매핑이 단일 진실원(SOT). 누락 ontology ID 는 경고 후
    빈 집합 — 호출측이 기존 동작 폴백하거나 결측 처리.
    """
    port = get_ontology_port()
    out: set[str] = set()
    for oid in ontology_ids:
        terms = port.mapping("dart", oid)
        if not terms:
            logger.warning("DART ontology mapping missing: %s", oid)
        out.update(terms)
    return out


# IFRS 표준 account_id 로 매칭한다(계정명은 회사마다 편차가 커 신뢰 불가).
# 과거(≤2018경) 공시는 구 태그(ifrs_*, 언더스코어), 최근은 ifrs-full_* (하이픈)을 쓴다 —
# 둘 다 ontology dart mapping 에 포함되어 있어 별도 하드코딩 불필요.
_AID_REVENUE = _dart_account_ids("IS_REV_TOTAL")
# CIS(금융업)에서 합산에서 제외할 일반 매출 계정: ifrs-full_Revenue(영업수익)는 수수료·
# 이자·기타영업수익 구성요소의 합계라 함께 합산하면 이중계상된다(현대차증권 Q1 4,254억).
# 구성요소가 없는 CIS 문장(CIS-only 1,976종목)은 _parse_income_equity 후반부에서 폴백으로 사용.
_CIS_REVENUE_EXCLUDE = {"ifrs-full_Revenue", "ifrs_Revenue"}
# CIS 구성요소 합산 게이트: 금융업 전용 계정(수수료·보험수익)이 있어야 합산 대상.
# dart_OtherOperatingIncome(기타영업수익)은 증권사에선 revenue 구성요소지만 일반 기업
# CIS 에선 기타영업외수익(영업외)일 수 있어(319400) 게이트에서 제외한다. 게이트가 닫히면
# ifrs-full_Revenue(수익(매출액))가 단일 매출 — 이자수익 등 영업외 항목을 revenue 에
# 섞지 않는다(023440 삼성엔지니어링).
_CIS_REVENUE_COMPONENTS = {
    "ifrs-full_FeeAndCommissionIncome",
    "ifrs-full_InsuranceRevenue",
}
_AID_OP = _dart_account_ids("IS_OP_INCOME")
_AID_NI_OWNERS = _dart_account_ids("IS_NI_PARENT")
_AID_NI = _dart_account_ids("IS_NI_TOTAL")  # 지배주주 항목 없을 때 폴백
_AID_EPS = _dart_account_ids("IS_EPS_BASIC")
_AID_EQ_OWNERS = _dart_account_ids("BS_EQ_PARENT")
_AID_EQ = _dart_account_ids("BS_EQ_TOTAL")  # 지배주주 지분 없을 때 폴백
# CAPEX: 유형·무형자산 취득(CF 투자활동). 회사마다 유출 부호가 양/음 혼재라 abs 로 합산.
_AID_CAPEX = _dart_account_ids("CF_INV_PPE", "CF_INV_INTANG")
# 실효세율·부채비용 실측 계정.
_AID_TAX = _dart_account_ids("IS_TAX_TOTAL")
_AID_PRETAX = _dart_account_ids("IS_PBT_TOTAL")
_AID_INTEREST = _dart_account_ids("IS_NONOP_INT_EXP")
# 대형사 폴백: 손익에 이자비용 계정이 없으면(삼성 등) CF 이자지급으로 근사.
_AID_INTEREST_PAID_CF = _dart_account_ids("CF_OP_INTEREST_PAID", "CF_FIN_INTEREST_PAID")


def fetch_income_and_equity(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> tuple[IncomeEquity | None, IncomeEquity | None]:
    """DART 전체재무제표에서 매출·지배순이익·EPS·지배자본을 account_id 로 추출한다.

    연결(CFS)과 별도(OFS)를 각각 시도해 (cfs, ofs) 튜플로 반환한다.
    손익은 IS/CIS 어디에나 올 수 있어 account_id 로 잡는다. 실패·데이터없음이면 None.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None, None
    cfs_result: IncomeEquity | None = None
    ofs_result: IncomeEquity | None = None
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
            logger.warning(
                "dart income failed %s %sQ%s %s: %s", corp_code, year, quarter, fs_div, e
            )
            continue
        _raise_if_quota(data)
        if data.get("status") != "000":
            continue  # 013(데이터없음) → 다음 fs_div 시도
        parsed = _parse_income_equity(data.get("list", []))
        if fs_div == "CFS":
            cfs_result = parsed
        else:
            ofs_result = parsed
    return cfs_result, ofs_result


def _is_cis_statement(rows: list[dict]) -> bool:
    """CIS 기반(금융업) 문장인지. 증권·보험·은행은 IS 대신 CIS 로 손익을 보고한다.

    revenue 구성요소 합산을 금융업에만 적용하기 위한 스코프 판정. IS 도 함께 있으면
    (일반 제조업) 합산하지 않는다 — 매출 계정 1개만 매칭되므로 합산해도 동일값이지만
    이자수익 등 영업외 항목이 매출에 섞이는 것을 방지한다.
    """
    has_cis = any(r.get("sj_div") == "CIS" for r in rows)
    has_is = any(r.get("sj_div") == "IS" for r in rows)
    return has_cis and not has_is


def _parse_income_equity(rows: list[dict]) -> IncomeEquity:
    """손익·자본은 account_id(안정), 차입금·현금은 계정명(표준태그 불안정)으로 함께 뽑는다."""
    fin = IncomeEquity()
    is_cis = _is_cis_statement(rows)  # 금융업(CIS 기반) 여부 — revenue 합산 스코프.
    # 구성요소 합산은 운영 수익 구성요소(수수료·기타영업수익·보험수익)가 있을 때만.
    # 일반 기업이 CIS 형식으로 보고하면(023440) ifrs-full_Revenue(수익(매출액)) 단일 매출.
    cis_sum = is_cis and any((r.get("account_id") or "") in _CIS_REVENUE_COMPONENTS for r in rows)
    cis_rev_fallback = None  # CIS에 구성요소가 없을 때 쓸 ifrs-full_Revenue(영업수익) 후보.
    borrowings = 0.0
    got_borrowing = False
    capex = 0.0
    got_capex = False
    interest_cf = None  # 손익 이자비용 없을 때 CF 이자지급 폴백(대형사).
    for row in rows:
        aid = row.get("account_id") or ""
        nm = (row.get("account_nm") or "").replace(" ", "")
        sj = row.get("sj_div")
        amt = _amount(row)
        if amt is None:
            continue
        if aid in _AID_CAPEX:  # 유형+무형 취득 합산(유출, abs). CF 계정이라 손익/BS 매칭과 독립.
            capex += abs(amt)
            got_capex = True
            continue
        if aid in _AID_TAX and fin.income_tax is None:
            fin.income_tax = abs(amt)  # 비용(부호 혼재 방어)
            continue
        if aid in _AID_PRETAX and fin.pretax_income is None:
            fin.pretax_income = amt
            continue
        if aid in _AID_INTEREST and fin.interest_expense is None:
            fin.interest_expense = abs(amt)  # 손익 이자비용 우선
            continue
        if aid in _AID_INTEREST_PAID_CF and interest_cf is None:
            interest_cf = abs(amt)  # CF 이자지급(폴백 후보)
            continue
        # 지배주주 항목을 우선하되(덮어쓰기), 없으면 전체 항목으로 채운다(setdefault 성격).
        # CF 행은 revenue 후보에서 제외 — ifrs-full_RevenueFromInterest(이자수익)가
        # CF(이자 수취)에도 나타나 손익의 매출액을 가로채는 사고(001550: CIS 매출액 252억
        # vs CF 이자수익 -0.21억)를 막는다. revenue 는 손익(IS/CIS)에서만 뽑는다.
        if sj != "CF" and aid in _AID_REVENUE and fin.revenue is None:
            if aid in _CIS_REVENUE_EXCLUDE:
                if cis_sum:
                    # 금융업: ifrs-full_Revenue(영업수익 합계)는 구성요소와 이중계상 →
                    # 합산에서 제외하고 폴백 후보로만 보관한다.
                    if cis_rev_fallback is None:
                        cis_rev_fallback = amt
                else:
                    # 일반 기업 CIS: ifrs-full_Revenue(수익(매출액))가 단일 매출.
                    fin.revenue = amt
            elif cis_sum:
                fin.revenue = amt  # 금융업: 첫 구성요소(수수료·이자·기타영업수익)
            # else: 일반 기업 CIS 의 이자수익 등 영업외 항목 — revenue 아님, skip.
        elif sj != "CF" and aid in _AID_REVENUE and cis_sum:
            if aid in _CIS_REVENUE_EXCLUDE:
                continue  # 영업수익 합계 행 — 구성요소와 이중계상이라 제외.
            # 증권·금융업(CIS 기반)은 단일 매출액 항목이 없어 구성요소(수수료·이자·
            # 기타영업수익)를 합산한다. 일반 제조업(IS 기반)은 매출 계정 1개만 매칭되므로
            # 합산해도 동일값 — 전역 회귀 없음.
            fin.revenue += amt
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
        elif (
            sj == "BS"
            and any(k in nm for k in ("단기차입금", "장기차입금", "사채", "유동성장기부채"))
            and "누계" not in nm
        ):
            borrowings += amt
            got_borrowing = True
    if got_borrowing:
        fin.borrowings = borrowings
    if got_capex:
        fin.capex = capex
    if fin.revenue is None and cis_rev_fallback is not None:
        # CIS에 구성요소가 없으면 영업수익 합계로 폴백(CIS-only 문장 회귀 방지).
        fin.revenue = cis_rev_fallback
    if fin.interest_expense is None and interest_cf is not None:
        fin.interest_expense = interest_cf  # 손익 이자비용 없으면 CF 이자지급으로 폴백
    return fin


def parse_full_statements(rows: list[dict]) -> dict[str, list[dict]]:
    """DART fnlttSinglAcntAll 응답 list 를 sj_div(BS/IS/CIS/CF)로 그룹화.

    각 항목: {account_id, name(account_nm), amount, sj_div, level}
    level: 0=대분류(합계·총계·소계), 1=중분류, 2=세부항목.
    소계·합계 행은 제외(중복 합계 방지). amount 가 없는 항목도 제외.
    """
    groups: dict[str, list[dict]] = {"BS": [], "IS": [], "CIS": [], "CF": [], "SCE": []}
    _TOTAL_KEYWORDS = ("합계", "총계", "소계", "계")
    for row in rows:
        sj = row.get("sj_div") or ""
        if sj not in groups:
            continue
        nm = (row.get("account_nm") or "").strip()
        if not nm:
            continue
        amt = _amount(row)
        if amt is None:
            continue
        # 소계·합계 행 제외(account_nm 끝에 '합계'/'총계'/'소계'/'계').
        # 단 account_id 가 실제 IFRS/dart 표준 요소면(미사용/빈값 아니면) 보존 — '자본총계'
        # (ifrs-full_Equity) 처럼 총계 이름의 공식 요소는 파싱·표시에 필요하기 때문.
        aid = (row.get("account_id") or "").strip()
        has_real_aid = bool(aid) and aid != "-표준계정코드 미사용-"
        if any(nm.endswith(kw) for kw in _TOTAL_KEYWORDS) and not has_real_aid:
            continue
        item: dict = {
            "account_id": aid,
            "name": nm,
            "amount": amt,
            "sj_div": sj,
        }
        # 자본변동표(SCE)는 account_nm × account_detail matrix 로 쓰인다.
        # account_detail 을 보존하고 level 은 사용하지 않는다.
        if sj == "SCE":
            item["detail"] = (row.get("account_detail") or "").strip()
            groups[sj].append(item)
            continue
        # level 판정: 주요 계정명(대분류)은 level=0, 나머지는 level=1.
        # IFRS 재무제표 표준 계정과목 기준. 정확한 시작일치로 오탐(기타유동자산→유동자산) 방지.
        _MAJOR_PREFIXES = (
            # BS(재무상태표)
            "유동자산",
            "비유동자산",
            "자산총계",
            "유동부채",
            "비유동부채",
            "부채총계",
            "자본금",
            "자본잉여금",
            "이익잉여금",
            "자본총계",
            "현금및현금성자산",
            "매출채권",
            "재고자산",
            "유형자산",
            "무형자산",
            "투자자산",
            "단기차입금",
            "장기차입금",
            "매입채무",
            # IS/CIS(손익계산서)
            "수익(매출액)",
            "매출원가",
            "매출총이익",
            "판매비와관리비",
            "영업이익(",
            "영업외수익",
            "영업외비용",
            "법인세비용차감전순이익",
            "법인세비용",
            "당기순이익",
            "총포괄손익",
            "지배기업의 소유주",
            # CF(현금흐름표)
            "영업활동현금흐름",
            "투자활동현금흐름",
            "재무활동현금흐름",
            "기초현금및현금성자산",
            "기말현금및현금성자산",
        )
        level = (
            0
            if any(nm.startswith(p) for p in _MAJOR_PREFIXES)
            or any(kw in nm for kw in ("합계", "총계"))
            else 1
        )
        item["level"] = level
        groups[sj].append(item)
    return {k: v for k, v in groups.items() if v}


def _fetch_full_statements_for_fs_div(
    api_key: str, corp_code: str, year: int, quarter: int, fs_div: str, session: requests.Session
) -> dict[str, list[dict]] | None:
    """특정 fs_div(CFS/OFS)의 전체재무제표를 조회해 sj_div별로 그룹화."""
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
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
    except (requests.RequestException, ValueError):
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    parsed = parse_full_statements(data.get("list", []))
    return parsed if parsed else None


def fetch_full_statements(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> dict[str, list[dict]] | None:
    """DART 전체재무제표(fnlttSinglAcntAll)를 조회해 sj_div별로 그룹화.

    연결(CFS) 우선, 없으면 별도(OFS). 실패·데이터없음이면 None.
    """
    for fs_div in ("CFS", "OFS"):
        parsed = _fetch_full_statements_for_fs_div(
            api_key, corp_code, year, quarter, fs_div, session
        )
        if parsed:
            return parsed
    return None


def fetch_full_statements_ofs(
    api_key: str, corp_code: str, year: int, quarter: int, session: requests.Session
) -> dict[str, list[dict]] | None:
    """별도재무제표(OFS)만 조회."""
    return _fetch_full_statements_for_fs_div(api_key, corp_code, year, quarter, "OFS", session)


def fetch_full_statements_by_div(
    api_key: str, corp_code: str, year: int, quarter: int, fs_div: str, session: requests.Session
) -> dict[str, list[dict]] | None:
    """특정 fs_div(CFS/OFS)의 전체재무제표를 조회."""
    return _fetch_full_statements_for_fs_div(api_key, corp_code, year, quarter, fs_div, session)


def fetch_income_summary(
    api_key: str, corp_code: str, year: int, quarter: int, fs_div: str, session: requests.Session
) -> IncomeEquity | None:
    """fnlttSinglAcnt(단일 재무제표) 폴백 — fnlttSinglAcntAll 이 013(데이터없음)인 CFS 기간용.

    응답에 CFS(첫 번째)·OFS(두 번째) 재무제표가 모두 포함되고 fs_div 는 무시되므로,
    두 번째 자본총계(별도 시작) 앞의 CFS 문장만 파싱한다. EPS 는 단일 API 가 제공하지
    않는다(eps=None). 계정명 매칭: 매출액/영업수익·영업이익(손실)·당기순이익(손실)·자본총계.
    """
    reprt_code = DART_REPORT_CODES.get(quarter)
    if not reprt_code:
        return None
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    try:
        resp = dart_throttle.get(session, _FNLTT_SINGL_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    _raise_if_quota(data)
    if data.get("status") != "000":
        return None
    items = data.get("list") or []
    # 두 번째 자본총계(별도 재무제표 시작) 앞까지만 CFS 로 취급.
    seen_equity = 0
    cfs_items: list[dict] = []
    for item in items:
        if item.get("account_nm") == "자본총계":
            seen_equity += 1
            if seen_equity == 2:
                break
        cfs_items.append(item)
    revenue = operating_income = net_income = equity = None
    for item in cfs_items:
        nm = item.get("account_nm")
        amt = _amount(item)
        if amt is None:
            continue
        if nm in ("매출액", "영업수익") and revenue is None:
            revenue = amt
        elif nm.startswith("영업이익") and operating_income is None:
            operating_income = amt
        elif nm.startswith("당기순이익") and net_income is None:
            net_income = amt
        elif nm == "자본총계" and equity is None:
            equity = amt
    if revenue is None and operating_income is None and net_income is None and equity is None:
        return None
    return IncomeEquity(
        revenue=revenue,
        operating_income=operating_income,
        net_income=net_income,
        eps=None,
        equity=equity,
    )


@dataclass
class CorpMapping:
    stock_code: str
    corp_code: str
    corp_name: str
    induty_code: str | None = (
        None  # DART 표준산업분류코드(corpCode.xml induty_code). 무료 산업분류.
    )


def fetch_corp_mappings(api_key: str, session: requests.Session) -> list[CorpMapping]:
    """corpCode.xml(zip) 을 받아 상장사(stock_code 보유) 매핑만 반환한다.

    induty_code(DART 표준산업분류)를 함께 캡처 — 별도 API 호출 없이 corpCode.xml 에 포함된
    무료 산업분류로 GICS 2차 anchor(mappings/dart_industry.yaml)에 사용.
    """
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
            induty = (item.findtext("induty_code") or "").strip() or None
            mappings.append(
                CorpMapping(
                    stock_code,
                    corp_code,
                    (item.findtext("corp_name") or "").strip(),
                    induty_code=induty,
                )
            )
    return mappings


@dataclass
class SegmentRow:
    """iotHom3MdQe 부문별 매출 행. segment_type: 산업/제품/지역/매출형태 구분코드."""

    bsns_year: str
    report_code: str
    segment_type: str  # DART 기준: 산업(I)/제품(P)/지역(G)/매출형태(S) — 공시 원문 구분값 그대로.
    segment_name: str
    revenue: float | None = None  # 부문 매출액(원)
    ratio_pct: float | None = None  # 총 매출 대비 비중(%)


def fetch_segment_sales(
    api_key: str,
    corp_code: str,
    year: int,
    report_code: str,
    session: requests.Session,
) -> list[SegmentRow]:
    """iotHom3MdQe(부문별 매출) 조회 → SegmentRow 목록. 사업보고서 연간 기준.

    사업보고서(11011) 원문의 부문별 매출을 구조화해 반환 — 제품/지역/부문 매출 비중의 유일한
    정형 DART 소스. 실패·데이터없음·한도초과 시 빈 리스트(조립 중단 아님 — 부문 매출은 보강 정보).
    """
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
    }
    try:
        resp = dart_throttle.get(session, _SEGMENT_SALES_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart segment_sales failed %s %s: %s", corp_code, year, e)
        return []
    _raise_if_quota(data)
    if data.get("status") != "000":
        return []
    rows: list[SegmentRow] = []
    for r in data.get("list", []) or []:
        seg_type = (
            r.get("se") or ""
        ).strip()  # DART 응답의 구분 필드명(se) — 산업/제품/지역/매출형태
        # 부문명/매출액/비중 필드는 DART 가 응답에서 kwd 항목 배열로 주는 경우가 많아 값 추출은 관대히.
        seg_name = (r.get("category") or r.get("item") or r.get("segment_name") or "").strip()
        if not seg_type and not seg_name:
            continue
        rows.append(
            SegmentRow(
                bsns_year=str(year),
                report_code=report_code,
                segment_type=seg_type,
                segment_name=seg_name,
                revenue=_float_field(r, "thstrm_am"),
                ratio_pct=_float_field(r, "thstrm_rt"),
            )
        )
    return rows


def fetch_disclosures(
    api_key: str,
    corp_code: str,
    stock_code: str,
    begin: date,
    end: date,
    session: requests.Session,
    pblntf_ty: str | None = None,
) -> list[Disclosure]:
    """corp_code + 기간으로 공시 목록을 조회한다(페이지네이션 처리).

    pblntf_ty 를 주면 공시유형을 서버에서 거른다(예: 'B'=주요사항보고 DS005 유증·CB·합병 등
    정형 공시). 기본 None 은 전체 유형(기존 호출부 무영향).
    """
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
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
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
    begin, end = (
        (f"{year + 1}0101", f"{year + 1}0930")
        if kind == "annual"
        else (f"{year}0301", f"{year + 1}0331")
    )
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
        r
        for r in data.get("list", [])
        if keyword in (r.get("report_nm") or "") and tag in (r.get("report_nm") or "")
    ]
    if not matches:
        return None
    # 접수일 최신순(정정 반영). rcept_no 는 시간순 증가라 최대값이 최신.
    return max(matches, key=lambda r: r.get("rcept_no", "")).get("rcept_no")


def find_all_periodic_reports(
    api_key: str, corp_code: str, bgn_de: str, session: requests.Session
) -> list[dict]:
    """기간 내 모든 사업·반기·분기보고서 공시 목록. 최신 접수(정정 반영) 우선.

    SCE 마이그레이션용: 종목당 1회 list.json 호출로 전체 보고서를 받아 rcept_no·report_nm
    (대상기간 'YYYY.MM' 태그) 을 취한다. end_de 는 오늘(호출 측에서 결정). status != 000 은
    빈 리스트로 돌려 조회 실패를 건너뛰게 한다.
    """
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": date.today().strftime("%Y%m%d"),
        "pblntf_ty": "A",  # 정기공시(사업/반기/분기보고서)
        "page_count": 100,
    }
    reports: list[dict] = []
    page = 1
    while True:
        params["page_no"] = page
        try:
            resp = dart_throttle.get(session, _LIST_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("dart periodic list failed %s: %s", corp_code, e)
            return []
        _raise_if_quota(data)
        if data.get("status") != "000":
            return []
        reports.extend(
            r
            for r in data.get("list", [])
            if any(
                kw in (r.get("report_nm") or "")
                for kw in ("사업보고서", "반기보고서", "분기보고서")
            )
        )
        if page >= data.get("total_page", 1):
            break
        page += 1
    # 접수일 최신순(정정 제출이 마지막 — 마이그레이션은 최신 원문을 우선 사용).
    return sorted(reports, key=lambda r: r.get("rcept_no", ""), reverse=True)


def find_ipo_reports(
    api_key: str, corp_code: str, bgn_de: str, session: requests.Session
) -> dict[str, str | None]:
    """발행공시(pblntf_ty=C)에서 최신 증권신고서·투자설명서 접수번호.

    신규 상장 종목처럼 사업보고서가 아직 없는 회사의 조립 소스 확보용. 각 키워드별
    가장 늦은 접수([기재정정] 포함)를 택한다. 조회 실패·없음은 값 None.
    """
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": date.today().strftime("%Y%m%d"),
        "pblntf_ty": "C",  # 발행공시
        "page_count": 100,
    }
    try:
        resp = dart_throttle.get(session, _LIST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("dart ipo list failed %s: %s", corp_code, e)
        return {"security": None, "invest": None}
    _raise_if_quota(data)
    if data.get("status") != "000":
        return {"security": None, "invest": None}

    out: dict[str, str | None] = {"security": None, "invest": None}
    for r in data.get("list", []):
        nm = r.get("report_nm") or ""
        for key, kw in (("security", "증권신고서"), ("invest", "투자설명서")):
            if kw in nm and (r.get("rcept_no") or "") > (out[key] or ""):
                out[key] = r["rcept_no"]
    return out


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

    fetch_report_zip 경유(MinIO 캐시-aside)라 동일 원문은 재다운로드하지 않는다.
    첨부가 여러 XML 이면 이어붙인다. 실패·빈 응답이면 빈 문자열(호출측은 제목-only 로 폴백).
    """
    raw = fetch_report_zip(api_key, rcept_no, session)
    if not raw:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
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
            session,
            _ELESTOCK_URL,
            params={"crtfc_key": api_key, "corp_code": corp_code},
            timeout=15,
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
