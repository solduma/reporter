"""DS002 주식총수(stockTotqySttus) 파싱 테스트 — 발행/자기/유통 주식수 추출."""

from __future__ import annotations

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


def test_fetch_stock_total_prefers_common_row():
    # 보통주 행을 우선 선택하고 콤마 숫자를 파싱한다.
    payload = {
        "status": "000",
        "list": [
            {
                "se": "보통주",
                "istc_totqy": "5,969,782,550",
                "tesstk_co": "0",
                "distb_stock_co": "5,969,782,550",
            },
            {
                "se": "우선주",
                "istc_totqy": "822,886,700",
                "tesstk_co": "0",
                "distb_stock_co": "822,886,700",
            },
        ],
    }
    total = dart.fetch_stock_total("key", "00126380", 2025, 4, _list_session(payload))
    assert total is not None
    assert total.issued == 5_969_782_550
    assert total.treasury == 0
    assert total.outstanding == 5_969_782_550


def test_fetch_stock_total_falls_back_to_sum_row_when_no_common():
    # 보통주 행이 없으면 '합계' 행을 쓴다.
    payload = {
        "status": "000",
        "list": [
            {"se": "합계", "istc_totqy": "1,000,000", "tesstk_co": "1,000", "distb_stock_co": "999,000"},
        ],
    }
    total = dart.fetch_stock_total("key", "x", 2025, 4, _list_session(payload))
    assert total is not None
    assert total.issued == 1_000_000
    assert total.treasury == 1_000
    assert total.outstanding == 999_000


def test_fetch_stock_total_parses_dash_and_blank_as_none():
    # '-'·빈 문자열은 None(파싱 불가) 으로 둔다.
    payload = {
        "status": "000",
        "list": [
            {"se": "보통주", "istc_totqy": "500", "tesstk_co": "-", "distb_stock_co": ""},
        ],
    }
    total = dart.fetch_stock_total("key", "x", 2025, 4, _list_session(payload))
    assert total is not None
    assert total.issued == 500
    assert total.treasury is None
    assert total.outstanding is None


def test_fetch_stock_total_empty_status_returns_none():
    # status != 000 (013 데이터없음 등) → None (상위가 KRX·스냅샷 폴백).
    total = dart.fetch_stock_total("key", "x", 2025, 4, _list_session({"status": "013"}))
    assert total is None


def test_fetch_stock_total_bad_quarter_returns_none():
    # 유효하지 않은 분기는 reprt_code 매핑이 없어 조회 없이 None.
    session = MagicMock()
    assert dart.fetch_stock_total("key", "x", 2025, 9, session) is None
    session.get.assert_not_called()


def test_fetch_stock_total_raises_on_quota():
    # 020(일일한도초과)은 데이터없음과 달리 예외로 올려 상위가 중단·구분하게 한다.
    payload = {"status": "020", "message": "사용한도를 초과하였습니다."}
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_stock_total("key", "x", 2025, 4, _list_session(payload))
