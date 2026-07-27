"""iotHom3MdQe(부문별 매출) + corpCode.xml induty_code 캡처 테스트."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from app.adapters import dart


def _list_session(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


# --- fetch_segment_sales(iotHom3MdQe) ---
def test_fetch_segment_sales_parses_rows():
    payload = {
        "status": "000",
        "list": [
            {"se": "제품", "category": "DRAM", "thstrm_am": "63,000,000,000", "thstrm_rt": "42.0"},
            {"se": "제품", "category": "NAND", "thstrm_am": "27,000,000,000", "thstrm_rt": "18.0"},
            {"se": "지역", "category": "해외", "thstrm_am": "102,000,000,000", "thstrm_rt": "68.0"},
        ],
    }
    rows = dart.fetch_segment_sales("key", "00126380", 2024, "11011", _list_session(payload))
    assert len(rows) == 3
    dram = rows[0]
    assert dram.segment_type == "제품"
    assert dram.segment_name == "DRAM"
    assert dram.revenue == 63_000_000_000.0
    assert dram.ratio_pct == 42.0
    assert dram.bsns_year == "2024"
    assert dram.report_code == "11011"
    # 지역 부문도 분류 보존.
    assert rows[2].segment_type == "지역"


def test_fetch_segment_sales_dash_blank_as_none():
    payload = {
        "status": "000",
        "list": [
            {"se": "제품", "category": "X", "thstrm_am": "-", "thstrm_rt": ""},
        ],
    }
    rows = dart.fetch_segment_sales("key", "x", 2024, "11011", _list_session(payload))
    assert rows[0].revenue is None
    assert rows[0].ratio_pct is None


def test_fetch_segment_sales_empty_status_returns_empty():
    rows = dart.fetch_segment_sales("key", "x", 2024, "11011", _list_session({"status": "013"}))
    assert rows == []


def test_fetch_segment_sales_raises_on_quota():
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_segment_sales("key", "x", 2024, "11011", _list_session({"status": "020"}))


def test_fetch_segment_sales_skips_empty_rows():
    payload = {
        "status": "000",
        "list": [{"se": "", "category": ""}, {"se": "제품", "category": "DRAM"}],
    }
    rows = dart.fetch_segment_sales("key", "x", 2024, "11011", _list_session(payload))
    assert len(rows) == 1


# --- fetch_corp_mappings induty_code 캡처 ---
def _corpcode_zip_bytes() -> bytes:
    xml = (
        "<result><list>"
        "<stock_code>005930</stock_code><corp_code>00126380</corp_code>"
        "<corp_name>삼성전자</corp_name><induty_code>C28</induty_code>"
        "</list>"
        "<list>"
        "<stock_code>000660</stock_code><corp_code>00164742</corp_code>"
        "<corp_name>SK하이닉스</corp_name><induty_code>C28</induty_code>"
        "</list>"
        "<list>"
        "<stock_code></stock_code><corp_code>99999999</corp_code>"
        "<corp_name>비상장</corp_name><induty_code>C26</induty_code>"
        "</list>"
        "</result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def test_fetch_corp_mappings_captures_induty_code():
    resp = MagicMock()
    resp.content = _corpcode_zip_bytes()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp

    mappings = dart.fetch_corp_mappings("key", session)
    # 상장사(stock_code 보유) 2건만.
    assert len(mappings) == 2
    samsung = mappings[0]
    assert samsung.stock_code == "005930"
    assert samsung.corp_code == "00126380"
    assert samsung.corp_name == "삼성전자"
    assert samsung.induty_code == "C28"


def test_fetch_corp_mappings_induty_none_when_missing():
    xml = (
        "<result><list>"
        "<stock_code>035420</stock_code><corp_code>00136980</corp_code>"
        "<corp_name>NAVER</corp_name>"
        "</list></result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    resp = MagicMock()
    resp.content = buf.getvalue()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp

    mappings = dart.fetch_corp_mappings("key", session)
    assert mappings[0].induty_code is None


def test_fetch_corp_mappings_bad_zip_returns_empty():
    resp = MagicMock()
    resp.content = b"not a zip"
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    assert dart.fetch_corp_mappings("key", session) == []
