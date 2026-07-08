"""유니버스 크롤러 단위 테스트 — marketValue 파싱·결측 방어를 목킹으로 검증한다."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services import universe


def _session(pages: list[dict]) -> MagicMock:
    """페이지별 payload 를 순서대로 반환하는 세션."""
    resps = []
    for payload in pages:
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = payload
        resps.append(r)
    session = MagicMock()
    session.get.side_effect = resps
    return session


def _stock(code, name, cap, tv, etype="stock", rate="N/A"):
    return {
        "itemCode": code,
        "stockName": name,
        "stockEndType": etype,
        "closePriceRaw": "10000",
        "fluctuationsRatio": "1.5",
        "marketValueRaw": cap,
        "accumulatedTradingValueRaw": tv,
        "threeMonthEarningRate": rate,
    }


def test_parses_fields_and_stock_type():
    payload = {"totalCount": 1, "stocks": [_stock("123456", "테스트", "480000000000", "5400000000")]}
    rows = universe.fetch_market("KOSDAQ", _session([payload]))
    assert len(rows) == 1
    r = rows[0]
    assert r.stock_code == "123456"
    assert r.market == "KOSDAQ"
    assert r.stock_type == "stock"
    assert r.market_cap == 480000000000
    assert r.trading_value == 5400000000
    assert r.change_pct == 1.5


def test_na_momentum_becomes_none():
    # threeMonthEarningRate 'N/A' → None (필터에서 안전 제외되도록)
    payload = {"totalCount": 1, "stocks": [_stock("1", "A", "1000", "1", rate="N/A")]}
    rows = universe.fetch_market("KOSDAQ", _session([payload]))
    assert rows[0].three_month_rate is None


def test_numeric_momentum_parsed():
    payload = {"totalCount": 1, "stocks": [_stock("1", "A", "1000", "1", rate="34.2")]}
    rows = universe.fetch_market("KOSDAQ", _session([payload]))
    assert rows[0].three_month_rate == 34.2


def test_etf_type_preserved():
    payload = {"totalCount": 1, "stocks": [_stock("2", "KODEX", "1000", "1", etype="etf")]}
    rows = universe.fetch_market("KOSPI", _session([payload]))
    assert rows[0].stock_type == "etf"


def test_pagination_stops_at_total():
    # totalCount 2, pageSize 기본이지만 2건이면 1페이지에서 멈춤(다음 페이지 호출 안 함)
    payload = {"totalCount": 2, "stocks": [_stock("1", "A", "1", "1"), _stock("2", "B", "2", "2")]}
    session = _session([payload])
    rows = universe.fetch_market("KOSDAQ", session)
    assert len(rows) == 2
    assert session.get.call_count == 1


def test_row_without_code_skipped():
    payload = {"totalCount": 1, "stocks": [{"stockName": "코드없음"}]}
    rows = universe.fetch_market("KOSDAQ", _session([payload]))
    assert rows == []
