"""미국 지수 fetch 단위 테스트 — 네이버 응답 목킹."""

from unittest.mock import MagicMock

import pytest

from reporter import us_market


@pytest.fixture(autouse=True)
def _reset_cache():
    # 프로세스 인메모리 캐시가 테스트 간 누수되지 않게 초기화
    us_market._us_cache = None
    us_market._kr_cache = None
    us_market._proxy_cache = None
    yield
    us_market._us_cache = None
    us_market._kr_cache = None
    us_market._proxy_cache = None


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


def test_cache_avoids_refetch():
    payloads = {".DJI": {"closePrice": "1", "compareToPreviousPrice": {"code": "2"}},
                ".IXIC": {"closePrice": "2", "compareToPreviousPrice": {"code": "2"}},
                ".INX": {"closePrice": "3", "compareToPreviousPrice": {"code": "2"}}}
    s1 = _session(payloads)
    first = us_market.fetch_us_indices(s1)
    calls_after_first = s1.get.call_count
    # 두 번째 호출은 캐시 → 새 세션을 안 씀
    s2 = _session(payloads)
    second = us_market.fetch_us_indices(s2)
    assert [q.close for q in first] == [q.close for q in second]
    assert s2.get.call_count == 0  # 캐시 히트 → 네트워크 없음
    assert calls_after_first == 3


def test_parses_kr_indices():
    payloads = {
        "KOSPI": {"closePrice": "7,291.91", "compareToPreviousClosePrice": "45.12",
                  "fluctuationsRatio": "0.62", "compareToPreviousPrice": {"code": "2"}},
        "KOSDAQ": {"closePrice": "794.00", "compareToPreviousClosePrice": "-9.00",
                   "fluctuationsRatio": "-1.15", "compareToPreviousPrice": {"code": "5"}},
    }
    quotes = us_market.fetch_kr_indices(_session(payloads))
    assert [q.name for q in quotes] == ["코스피", "코스닥"]
    kospi, kosdaq = quotes
    assert kospi.close == "7,291.91" and kospi.rising is True
    assert kosdaq.rising is False


def test_kr_and_us_caches_are_independent():
    us_payloads = {".DJI": {"closePrice": "1", "compareToPreviousPrice": {"code": "2"}},
                   ".IXIC": {"closePrice": "2", "compareToPreviousPrice": {"code": "2"}},
                   ".INX": {"closePrice": "3", "compareToPreviousPrice": {"code": "2"}}}
    kr_payloads = {"KOSPI": {"closePrice": "10", "compareToPreviousPrice": {"code": "2"}},
                   "KOSDAQ": {"closePrice": "20", "compareToPreviousPrice": {"code": "5"}}}
    us_market.fetch_us_indices(_session(us_payloads))
    # 미국 캐시가 채워져도 국내는 별도 캐시라 새로 조회한다.
    kr_session = _session(kr_payloads)
    kr = us_market.fetch_kr_indices(kr_session)
    assert [q.name for q in kr] == ["코스피", "코스닥"]
    assert kr_session.get.call_count == 2


def test_map_industry_to_proxy_keywords():
    assert us_market.map_industry_to_proxy("반도체와반도체장비") == ".SOX"
    assert us_market.map_industry_to_proxy("디스플레이") == ".SOX"
    assert us_market.map_industry_to_proxy("소프트웨어") == ".IXIC"
    assert us_market.map_industry_to_proxy("게임엔터테인먼트") == ".IXIC"
    assert us_market.map_industry_to_proxy("은행") == ".INX"  # 미매칭 → 대형주


def test_map_industry_to_proxy_market_fallback():
    # 업종 라벨 없으면 시장으로 폴백: KOSDAQ→기술주, 그 외→대형주.
    assert us_market.map_industry_to_proxy(None, market="KOSDAQ") == ".IXIC"
    assert us_market.map_industry_to_proxy(None, market="KOSPI") == ".INX"
    assert us_market.map_industry_to_proxy(None) == ".INX"


def test_fetch_us_sector_proxies():
    payloads = {".SOX": {"closePrice": "5,000", "compareToPreviousPrice": {"code": "2"}},
                ".IXIC": {"closePrice": "25,000", "compareToPreviousPrice": {"code": "2"}},
                ".INX": {"closePrice": "7,000", "compareToPreviousPrice": {"code": "5"}}}
    quotes = us_market.fetch_us_sector_proxies(_session(payloads))
    assert [q.name for q in quotes] == ["미국 반도체", "미국 기술주", "미국 대형주"]
