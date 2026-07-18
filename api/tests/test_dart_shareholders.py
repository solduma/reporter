"""DS005 최대주주 현황(hyslrSttus) 파싱 테스트 — 최대주주명·특수관계인 합산 지분율."""

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


# 개인·법인이 여러 행으로 분산(보통주/우선주 등). 기말 지분율을 전 행 합산해 지배지분 근사.
_SAMSUNG_2024 = {
    "status": "000",
    "list": [
        {"nm": "삼성생명보험㈜", "relate": "최대주주 본인", "trmend_posesn_stock_qota_rt": "8.51"},
        {"nm": "홍라희", "relate": "최대주주의 특수관계인", "trmend_posesn_stock_qota_rt": "1.64"},
        {"nm": "이재용", "relate": "최대주주의 특수관계인", "trmend_posesn_stock_qota_rt": "1.63"},
        {"nm": "삼성생명보험㈜", "relate": "최대주주 본인", "trmend_posesn_stock_qota_rt": "0.01"},
    ],
}


def test_fetch_largest_shareholders_sums_group_stake():
    # 최대주주명은 '최대주주 본인' 행의 nm, 지분율은 전 행 합산.
    r = dart.fetch_largest_shareholders("key", "00126380", 2024, 4, _list_session(_SAMSUNG_2024))
    assert r is not None
    assert r.top_holder == "삼성생명보험㈜"
    assert r.group_stake_pct == 11.79  # 8.51+1.64+1.63+0.01


def test_fetch_largest_shareholders_falls_back_to_first_row_for_name():
    # '최대주주 본인' 라벨이 없으면 첫 행을 최대주주로.
    payload = {
        "status": "000",
        "list": [
            {"nm": "김대표", "relate": "발행회사 임원", "trmend_posesn_stock_qota_rt": "30.00"},
        ],
    }
    r = dart.fetch_largest_shareholders("key", "x", 2024, 4, _list_session(payload))
    assert r is not None
    assert r.top_holder == "김대표"
    assert r.group_stake_pct == 30.0


def test_fetch_largest_shareholders_zero_stake_returns_none():
    # 지분율 합이 0이면(파싱 실패·이상치) None → 상위가 원문 서술 폴백.
    payload = {"status": "000", "list": [{"nm": "x", "relate": "최대주주 본인", "trmend_posesn_stock_qota_rt": "-"}]}
    assert dart.fetch_largest_shareholders("key", "x", 2024, 4, _list_session(payload)) is None


def test_fetch_largest_shareholders_empty_status_returns_none():
    assert dart.fetch_largest_shareholders("key", "x", 2024, 4, _list_session({"status": "013"})) is None


def test_fetch_largest_shareholders_bad_quarter_returns_none():
    session = MagicMock()
    assert dart.fetch_largest_shareholders("key", "x", 2024, 9, session) is None
    session.get.assert_not_called()


def test_fetch_largest_shareholders_raises_on_quota():
    payload = {"status": "020", "message": "사용한도를 초과하였습니다."}
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_largest_shareholders("key", "x", 2024, 4, _list_session(payload))
