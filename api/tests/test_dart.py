"""DART 서비스 단위 테스트 — corpCode 매핑·공시 목록 파싱을 목킹으로 검증한다."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from unittest.mock import MagicMock

from app.services import dart

_CORPCODE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<result>
<list><corp_code>00126380</corp_code><corp_name>\xec\x82\xbc\xec\x84\xb1\xec\xa0\x84\xec\x9e\x90</corp_name><stock_code>005930</stock_code></list>
<list><corp_code>00164779</corp_code><corp_name>\xed\x95\x9c\xea\xb5\xad</corp_name><stock_code></stock_code></list>
</result>"""


def _zip_session() -> MagicMock:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", _CORPCODE_XML)
    resp = MagicMock()
    resp.content = buf.getvalue()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_corp_mappings_keeps_only_listed():
    # stock_code 있는 상장사만 반환(빈 stock_code 는 제외)
    mappings = dart.fetch_corp_mappings("key", _zip_session())
    assert len(mappings) == 1
    assert mappings[0].stock_code == "005930"
    assert mappings[0].corp_code == "00126380"
    assert mappings[0].corp_name == "삼성전자"


def _list_session(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_disclosures_parses_and_builds_url():
    payload = {
        "status": "000",
        "total_page": 1,
        "list": [
            {
                "rcept_no": "20260707000403",
                "report_nm": "주요사항보고서(자기주식처분결정)",
                "flr_nm": "삼성전자",
                "rcept_dt": "20260707",
            }
        ],
    }
    discs = dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8), _list_session(payload)
    )
    assert len(discs) == 1
    d = discs[0]
    assert d.rcept_no == "20260707000403"
    assert d.rcept_dt == date(2026, 7, 7)
    assert d.stock_code == "005930"
    assert "20260707000403" in d.dart_url


def test_fetch_disclosures_empty_status_returns_empty():
    # status != 000 (예: 013 데이터없음) → 빈 리스트
    discs = dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8),
        _list_session({"status": "013", "message": "조회된 데이타가 없습니다."}),
    )
    assert discs == []
