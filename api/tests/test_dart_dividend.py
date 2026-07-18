"""DS002 배당에관한사항(alotMatter) 파싱 테스트 — 보통주 주당현금배당금·현금배당수익률."""

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


# 실제 alotMatter 응답 형태(삼성전자 2024)를 축약. se 는 공백 포함, 보통주/우선주 분리.
_SAMSUNG_2024 = {
    "status": "000",
    "list": [
        {"se": "(연결)현금배당성향(%)", "stock_knd": "-", "thstrm": "29.20"},
        {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "2.70", "frmtrm": "1.90"},
        {"se": "현금배당수익률(%)", "stock_knd": "우선주", "thstrm": "3.30"},
        {"se": "주식배당수익률(%)", "stock_knd": "보통주", "thstrm": "-"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,446", "frmtrm": "1,444"},
        {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,447"},
        {"se": "주당 주식배당(주)", "stock_knd": "보통주", "thstrm": "-"},
    ],
}


def test_fetch_dividend_picks_common_stock_current_term():
    # 보통주 행의 당기(thstrm) 값을 뽑고, 우선주·주식배당·배당성향과 섞이지 않는다.
    div = dart.fetch_dividend("key", "00126380", 2024, 4, _list_session(_SAMSUNG_2024))
    assert div is not None
    assert div.dps == 1446.0  # 보통주(우선주 1447 아님)
    assert div.div_yield == 2.70  # 현금배당수익률(주식배당수익률 아님)


def test_fetch_dividend_falls_back_to_dash_stock_knd():
    # 주식종류 분리가 없으면(stock_knd '-') 그 행을 쓴다.
    payload = {
        "status": "000",
        "list": [
            {"se": "주당 현금배당금(원)", "stock_knd": "-", "thstrm": "500"},
            {"se": "현금배당수익률(%)", "stock_knd": "-", "thstrm": "1.20"},
        ],
    }
    div = dart.fetch_dividend("key", "x", 2024, 4, _list_session(payload))
    assert div is not None
    assert div.dps == 500.0
    assert div.div_yield == 1.20


def test_fetch_dividend_no_dividend_rows_returns_none():
    # 배당 항목이 없으면(무배당·미공시) None → 상위가 네이버 스크랩 폴백.
    payload = {"status": "000", "list": [{"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "100"}]}
    assert dart.fetch_dividend("key", "x", 2024, 4, _list_session(payload)) is None


def test_fetch_dividend_dash_dps_returns_none_field():
    # 무배당 연도: 주당배당금 '-' → dps None, 수익률만 있으면 그 값 유지.
    payload = {
        "status": "000",
        "list": [
            {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "-"},
            {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "-"},
        ],
    }
    assert dart.fetch_dividend("key", "x", 2024, 4, _list_session(payload)) is None


def test_fetch_dividend_empty_status_returns_none():
    assert dart.fetch_dividend("key", "x", 2024, 4, _list_session({"status": "013"})) is None


def test_fetch_dividend_bad_quarter_returns_none():
    session = MagicMock()
    assert dart.fetch_dividend("key", "x", 2024, 9, session) is None
    session.get.assert_not_called()


def test_fetch_dividend_raises_on_quota():
    payload = {"status": "020", "message": "사용한도를 초과하였습니다."}
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_dividend("key", "x", 2024, 4, _list_session(payload))
