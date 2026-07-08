"""미국 지수 fetch 단위 테스트 — 네이버 응답 목킹."""

from unittest.mock import MagicMock

from reporter import us_market


def _session(payloads: dict) -> MagicMock:
    """symbol → json payload 매핑으로 응답하는 세션."""
    def _get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        for sym, payload in payloads.items():
            if f"/{sym}/" in url:
                resp.json.return_value = payload
                return resp
        resp.json.return_value = {}
        return resp

    session = MagicMock()
    session.get.side_effect = _get
    return session


def test_parses_indices_with_direction():
    payloads = {
        ".DJI": {"closePrice": "52,925.15", "compareToPreviousClosePrice": "-130.76",
                 "fluctuationsRatio": "-0.25", "compareToPreviousPrice": {"code": "5"}},
        ".IXIC": {"closePrice": "25,818.69", "compareToPreviousClosePrice": "302.47",
                  "fluctuationsRatio": "1.16", "compareToPreviousPrice": {"code": "2"}},
        ".INX": {"closePrice": "7,503.85", "compareToPreviousClosePrice": "0",
                 "fluctuationsRatio": "0", "compareToPreviousPrice": {"code": "3"}},
    }
    quotes = us_market.fetch_us_indices(_session(payloads))
    assert len(quotes) == 3
    dow, nas, sp = quotes
    assert dow.name == "다우" and dow.close == "52,925.15" and dow.rising is False
    assert nas.name == "나스닥" and nas.rising is True  # code 2 = 상승
    assert sp.rising is None  # code 3 = 보합/판단불가


def test_skips_index_without_close():
    payloads = {".DJI": {}, ".IXIC": {"closePrice": "25,818.69"}, ".INX": {}}
    quotes = us_market.fetch_us_indices(_session(payloads))
    # close 없는 다우·S&P 는 빠지고 나스닥만
    assert [q.name for q in quotes] == ["나스닥"]
