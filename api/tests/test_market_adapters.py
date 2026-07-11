"""MarketData 어댑터 단위 테스트 — 팩토리 선택·US 30분봉 미지원(외부 IO 미호출)."""

from __future__ import annotations

from app.adapters.market import KrMarketDataAdapter, UsMarketDataAdapter, get_market_data


def test_factory_selects_by_market():
    assert isinstance(get_market_data("KR"), KrMarketDataAdapter)
    assert isinstance(get_market_data("US"), UsMarketDataAdapter)
    assert isinstance(get_market_data(), KrMarketDataAdapter)  # 기본 KR
    assert isinstance(get_market_data("XX"), KrMarketDataAdapter)  # 미지정은 KR 폴백


def test_us_intraday_is_empty_without_io():
    # 미국은 30분봉 소스가 없어 외부 호출 없이 즉시 빈 리스트.
    assert UsMarketDataAdapter().fetch_intraday_30min("SMH.O") == []
