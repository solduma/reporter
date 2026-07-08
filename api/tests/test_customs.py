"""관세청 무역통계 파싱 단위 테스트 — XML 국가 합산·총계행 제외를 검증한다."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services import customs

# 2026.01 두 국가 + 총계행. 월별 국가 합산, 총계행은 제외해야 한다.
_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><body><items>
<item><year>총계</year><expDlr>1000</expDlr><impDlr>400</impDlr><balPayments>600</balPayments><statCdCntnKor1>-</statCdCntnKor1></item>
<item><year>2026.01</year><expDlr>600</expDlr><impDlr>250</impDlr><balPayments>350</balPayments><statCdCntnKor1>미국</statCdCntnKor1></item>
<item><year>2026.01</year><expDlr>400</expDlr><impDlr>150</impDlr><balPayments>250</balPayments><statCdCntnKor1>중국</statCdCntnKor1></item>
<item><year>2026.02</year><expDlr>500</expDlr><impDlr>200</impDlr><balPayments>300</balPayments><statCdCntnKor1>미국</statCdCntnKor1></item>
</items>
<resultCode>00</resultCode><resultMsg>정상</resultMsg>
</body></response>"""


def _session(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = text.encode()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_aggregates_countries_by_month_excluding_total():
    rows = customs.fetch_trade_by_hs("key", "8542", "202601", "202602", _session(_XML))
    # 2026.01 = 미국+중국 합산, 2026.02 = 미국. '총계' 행은 시계열에서 제외.
    assert [r.period for r in rows] == ["2026.01", "2026.02"]
    jan = rows[0]
    assert jan.export_usd == 1000  # 600+400
    assert jan.import_usd == 400   # 250+150
    assert jan.balance_usd == 600  # 수출-수입 재계산


def test_balance_is_export_minus_import():
    rows = customs.fetch_trade_by_hs("key", "8542", "202602", "202602", _session(_XML))
    feb = next(r for r in rows if r.period == "2026.02")
    assert feb.balance_usd == feb.export_usd - feb.import_usd == 300


def test_non_ok_result_returns_empty():
    xml = '<response><body><resultCode>03</resultCode><resultMsg>없음</resultMsg></body></response>'
    assert customs.fetch_trade_by_hs("key", "8542", "202601", "202601", _session(xml)) == []


def test_malformed_xml_returns_empty():
    assert customs.fetch_trade_by_hs("key", "8542", "202601", "202601", _session("<not-closed")) == []
