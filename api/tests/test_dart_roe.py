"""DS003 단일회사 주요재무지표(fnlttSinglIndx) 파싱 테스트 — 수익성지표 ROE 추출."""

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


# 실제 fnlttSinglIndx(수익성 M210000, 삼성전자 2024) 축약. idx_val 이 null 인 행도 섞인다.
_SAMSUNG_2024 = {
    "status": "000",
    "list": [
        {"idx_cl_nm": "수익성지표", "idx_nm": "순이익률", "idx_val": "11.451"},
        {"idx_cl_nm": "수익성지표", "idx_nm": "세전계속사업이익률", "idx_val": None},
        {"idx_cl_nm": "수익성지표", "idx_nm": "ROE", "idx_val": "8.997"},
        {"idx_cl_nm": "수익성지표", "idx_nm": "자기자본영업이익률", "idx_val": "8.546"},
    ],
}


def test_fetch_roe_extracts_exact_roe_row():
    # 'ROE' 완전일치 행만 잡고 '자기자본영업이익률' 등 유사어와 섞이지 않는다.
    roe = dart.fetch_roe("key", "00126380", 2024, 4, _list_session(_SAMSUNG_2024))
    assert roe == 8.997


def test_fetch_roe_null_value_returns_none():
    # ROE 행이 있어도 idx_val 이 null 이면 None.
    payload = {"status": "000", "list": [{"idx_nm": "ROE", "idx_val": None}]}
    assert dart.fetch_roe("key", "x", 2024, 4, _list_session(payload)) is None


def test_fetch_roe_no_roe_row_returns_none():
    # 수익성지표에 ROE 행이 없으면 None → 상위가 네이버 폴백.
    payload = {"status": "000", "list": [{"idx_nm": "순이익률", "idx_val": "11.4"}]}
    assert dart.fetch_roe("key", "x", 2024, 4, _list_session(payload)) is None


def test_fetch_roe_empty_status_returns_none():
    # 2023 3Q 이전은 013(데이터없음) → None → 네이버 폴백.
    assert dart.fetch_roe("key", "x", 2022, 4, _list_session({"status": "013"})) is None


def test_fetch_roe_bad_quarter_returns_none():
    session = MagicMock()
    assert dart.fetch_roe("key", "x", 2024, 9, session) is None
    session.get.assert_not_called()


def test_fetch_roe_sends_profitability_idx_code():
    # 수익성지표 분류코드(M210000)를 파라미터로 보낸다.
    session = _list_session(_SAMSUNG_2024)
    dart.fetch_roe("key", "00126380", 2024, 4, session)
    _, kwargs = session.get.call_args
    assert kwargs["params"]["idx_cl_code"] == "M210000"


def test_fetch_roe_raises_on_quota():
    payload = {"status": "020", "message": "사용한도를 초과하였습니다."}
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_roe("key", "x", 2024, 4, _list_session(payload))
