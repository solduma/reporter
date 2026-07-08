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
_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


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
