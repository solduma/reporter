"""수급 섹터 로테이션 스코어(sector_flow) 순수 로직 단위 테스트."""

from __future__ import annotations

from app.domain.technicals import Technicals
from app.services.sector_flow import flow_score, foreign_delta


def _tech(return_3m=None, near_high_pct=None, vol_ratio=None) -> Technicals:
    return Technicals(
        last_close=None, high_52w=None, near_high_pct=near_high_pct, ma20=None,
        ma60=None, ma120=None, ma_aligned=None, above_ma120=None,
        vol_ratio=vol_ratio, return_3m=return_3m, trend_score=None,
    )


def test_flow_score_high_for_strong_inflow():
    # 강한 추세 + 신고가권 + 거래량 급증 → 높은 점수.
    s = flow_score(_tech(return_3m=40, near_high_pct=100, vol_ratio=2.0), foreign_delta=1.0)
    assert s is not None and s >= 90


def test_flow_score_low_for_laggard():
    s = flow_score(_tech(return_3m=-20, near_high_pct=70, vol_ratio=0.5), foreign_delta=-1.0)
    assert s is not None and s <= 10


def test_flow_score_none_without_data():
    assert flow_score(_tech(), foreign_delta=None) is None


def test_flow_score_works_without_foreign():
    # 미국(외국인 없음)도 나머지 지표로 산출.
    s = flow_score(_tech(return_3m=10, near_high_pct=90, vol_ratio=1.0), foreign_delta=None)
    assert s is not None


def test_foreign_delta_change():
    # 20거래일 전 대비 최신 외국인비율 변화(pp).
    ratios = [None] + [10.0] * 20 + [12.0]  # 최신 12.0, 20일 전 10.0
    assert foreign_delta(ratios, lookback=20) == 2.0


def test_foreign_delta_insufficient_data():
    assert foreign_delta([None, None]) is None
    assert foreign_delta([5.0]) is None


def test_warm_cache_calls_flow_computations(monkeypatch):
    # warm_cache 는 양 시장 flow + 양 지수 flow 를 미리 호출해 캐시를 데운다.
    from app.services import sector_flow

    markets, indices = [], []
    monkeypatch.setattr(sector_flow, "compute_flows", lambda m, session=None: markets.append(m) or [])
    monkeypatch.setattr(
        sector_flow, "index_flow_score", lambda n, session=None: indices.append(n) or None
    )
    sector_flow.warm_cache()
    assert set(markets) == {"KR", "US"}
    assert set(indices) == {"KOSPI", "KOSDAQ"}
