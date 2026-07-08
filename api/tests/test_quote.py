"""quote 스크래퍼 단위 테스트 — 재무/동일업종 파싱을 목킹된 HTML 로 검증한다."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services import quote

_FIN_HTML = """
<div class="cop_analysis"><table>
<thead><tr><th>주요재무정보</th><th>2024.12</th><th>2025.12</th><th>2026.12(E)</th><th>2026.03</th></tr></thead>
<tbody>
<tr><th>매출액</th><td>3,008,709</td><td>3,336,059</td><td>7,272,368</td><td>1,338,734</td></tr>
<tr><th>영업이익</th><td>327,260</td><td>436,010</td><td>3,742,870</td><td>572,328</td></tr>
<tr><th>EPS(원)</th><td>4,000</td><td>2,864</td><td>45,690</td><td>6,993</td></tr>
<tr><th>PER(배)</th><td>10.5</td><td>18.27</td><td></td><td>13.51</td></tr>
<tr><th>PBR(배)</th><td>1.2</td><td>1.87</td><td></td><td>2.33</td></tr>
</tbody></table></div>
"""

_PEER_HTML = """
<div class="section trade_compare"><table>
<thead><tr><th>종목명</th><th>삼성전자*005930</th><th>SK하이닉스*000660</th></tr></thead>
<tbody>
<tr><th>현재가</th><td>279,000</td><td>2,117,000</td></tr>
<tr><th>PER(%)</th><td>22.55</td><td>20.45</td></tr>
<tr><th>PBR(배)</th><td>3.88</td><td>8.90</td></tr>
</tbody></table></div>
"""


def _session(html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_financials_parses_periods_and_estimate_flag():
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    assert [f.period for f in fins] == ["2024.12", "2025.12", "2026.12(E)", "2026.03"]
    assert fins[2].is_estimate is True
    assert fins[0].is_estimate is False


def test_financials_maps_row_labels_to_fields():
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    q1 = fins[-1]  # 2026.03
    assert q1.revenue == 1338734.0
    assert q1.operating_income == 572328.0
    assert q1.eps == 6993.0
    assert q1.per == 13.51
    assert q1.pbr == 2.33


def test_financials_empty_cell_is_none():
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    est = fins[2]  # 2026.12(E), PER/PBR 비어 있음
    assert est.per is None
    assert est.pbr is None


def test_peers_parses_code_name_and_values():
    peers = quote.fetch_peers("005930", _session(_PEER_HTML))
    assert len(peers) == 2
    assert peers[0].stock_code == "005930"
    assert peers[0].name == "삼성전자"
    assert peers[0].values["현재가"] == "279,000"
    assert peers[1].stock_code == "000660"
    assert peers[1].values["PER(%)"] == "20.45"


def test_num_strips_direction_and_symbols():
    assert quote._num("2,589,355") == 2589355.0
    assert quote._num("하향-5.83%") == -5.83
    assert quote._num("") is None
    assert quote._num("-") is None
