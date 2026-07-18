"""DART 관계사 수집(fetch_related_companies) — 모회사(hyslrSttus)+자회사(otrCprInvstmntSttus)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters import dart
from app.adapters.dart import client


def _url_session(by_url: dict[str, dict]) -> MagicMock:
    """URL 별로 다른 JSON 을 돌려주는 세션 목(fetch_related 는 hyslr+otrcpr 두 URL 호출)."""

    def _get(url, **kwargs):
        resp = MagicMock()
        resp.json.return_value = by_url.get(url, {"status": "013"})
        resp.raise_for_status = MagicMock()
        return resp

    session = MagicMock()
    session.get.side_effect = _get
    return session


def test_fetch_related_parent_and_subsidiary():
    # 모회사(법인 최대주주) + 자회사(50%+)/출자사, 합계 행 제외.
    session = _url_session({
        client._HYSLR_URL: {
            "status": "000",
            "list": [
                {"nm": "(주)가비아", "relate": "최대주주", "trmend_posesn_stock_qota_rt": "36.30"},
                {"nm": "전정완", "relate": "최대주주의 임원", "trmend_posesn_stock_qota_rt": "1.80"},
                {"nm": "계", "relate": None, "trmend_posesn_stock_qota_rt": "39.10"},
            ],
        },
        client._OTR_CPR_URL: {
            "status": "000",
            "list": [
                {"inv_prm": "㈜에스피소프트", "trmend_blce_qota_rt": "35.57"},
                {"inv_prm": "㈜종속법인", "trmend_blce_qota_rt": "80.00"},
                {"inv_prm": "합계", "trmend_blce_qota_rt": "-"},
            ],
        },
    })
    out = dart.fetch_related_companies("key", "00XX", 2025, 4, session)
    by_name = {r.name: r for r in out}
    assert by_name["(주)가비아"].relation == "parent"  # 법인 최대주주 → 모회사
    assert "전정완" not in by_name  # 개인 임원 제외
    assert "계" not in by_name and "합계" not in by_name  # 합계 행 제외
    assert by_name["㈜에스피소프트"].relation == "investor"  # 35.57% → investor
    assert by_name["㈜종속법인"].relation == "subsidiary"  # 80% → subsidiary


def test_fetch_related_individual_top_holder_no_parent():
    # 최대주주가 개인이면 모회사 없음(자회사만).
    session = _url_session({
        client._HYSLR_URL: {
            "status": "000",
            "list": [{"nm": "김대표", "relate": "최대주주", "trmend_posesn_stock_qota_rt": "40.0"}],
        },
        client._OTR_CPR_URL: {"status": "000", "list": [{"inv_prm": "㈜자회사", "trmend_blce_qota_rt": "60"}]},
    })
    out = dart.fetch_related_companies("key", "x", 2025, 4, session)
    assert all(r.relation != "parent" for r in out)  # 개인 → 모회사 없음
    assert any(r.name == "㈜자회사" and r.relation == "subsidiary" for r in out)


def test_fetch_related_bad_quarter_empty():
    session = MagicMock()
    assert dart.fetch_related_companies("key", "x", 2025, 9, session) == []
    session.get.assert_not_called()


def test_fetch_related_raises_on_quota():
    session = _url_session({client._HYSLR_URL: {"status": "020", "message": "사용한도를 초과"}})
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_related_companies("key", "x", 2025, 4, session)
