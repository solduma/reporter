"""quote 스크래퍼 단위 테스트 — 재무/동일업종 파싱을 목킹된 HTML 로 검증한다."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services import quote

# 실제 main.naver 구조 재현: 상단 그룹 행(연간 colspan=2 / 분기 colspan=3),
# 기간 행에 연간·분기가 섞이고 '2025.12' 가 양쪽에 중복 등장.
_FIN_HTML = """
<div class="cop_analysis"><table>
<thead>
<tr><th>주요재무정보</th><th colspan="2">최근 연간 실적</th><th colspan="3">최근 분기 실적</th></tr>
<tr><th>주요재무정보</th><th>2024.12</th><th>2025.12</th><th>2025.12</th><th>2026.03</th><th>2026.06(E)</th></tr>
</thead>
<tbody>
<tr><th>매출액</th><td>3,008,709</td><td>3,336,059</td><td>938,374</td><td>1,338,734</td><td>1,738,644</td></tr>
<tr><th>영업이익</th><td>327,260</td><td>436,010</td><td>200,737</td><td>572,328</td><td>850,494</td></tr>
<tr><th>EPS(원)</th><td>4,000</td><td>2,864</td><td>2,864</td><td>6,993</td><td>10,625</td></tr>
<tr><th>PER(배)</th><td>10.5</td><td>18.27</td><td>18.27</td><td>13.51</td><td></td></tr>
<tr><th>PBR(배)</th><td>1.2</td><td>1.87</td><td>1.87</td><td>2.33</td><td></td></tr>
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


def test_financials_returns_quarters_only():
    # 연간 컬럼(2024.12/2025.12)은 제외하고 분기 3개만 반환. 중복 2025.12 는 분기값.
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    assert [f.period for f in fins] == ["2025.12", "2026.03", "2026.06(E)"]
    assert all(f.period_type == "quarter" for f in fins)


def test_financials_quarterly_value_not_annual():
    # 중복 2025.12 에서 분기값(938,374)을 취해야 한다(연간 3,336,059 아님).
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    q_2025_12 = next(f for f in fins if f.period == "2025.12")
    assert q_2025_12.revenue == 938374.0
    assert q_2025_12.operating_income == 200737.0


def test_financials_maps_row_labels_to_fields():
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    q1 = next(f for f in fins if f.period == "2026.03")
    assert q1.revenue == 1338734.0
    assert q1.eps == 6993.0
    assert q1.per == 13.51
    assert q1.pbr == 2.33


def test_financials_empty_cell_is_none():
    fins = quote.fetch_financials("005930", _session(_FIN_HTML))
    est = next(f for f in fins if f.is_estimate)  # 2026.06(E), PER/PBR 비어 있음
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
