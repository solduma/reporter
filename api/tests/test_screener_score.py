"""스크리너 성장스코어·커버리지 라벨 순수 로직 단위 테스트."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.db.models import Base
from app.domain import scoring
from app.services import screener_service as screener


@dataclass
class _U:
    momentum_3m: float | None = None
    market: str | None = "KOSDAQ"
    trend_score: float | None = None


@dataclass
class _G:
    revenue_yoy: float | None
    op_yoy: float | None
    op_turnaround: bool


@dataclass
class _F:
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    ev_ebitda: float | None = None
    div_yield: float | None = None


def test_coverage_label():
    assert screener._coverage_label(0, 0) is None  # 커버 없음
    assert screener._coverage_label(3, 0) == "HOLD"  # 커버 있으나 BUY 없음
    assert screener._coverage_label(3, 2) == "BUY"  # BUY 있음


def test_percentile_ranker_monotonic():
    rank = scoring.percentile_ranker([10.0, 20.0, 30.0, 40.0])
    assert rank(10.0) == 0.0  # 최저 → 0
    assert rank(40.0) == 1.0  # 최고 → 1
    assert 0.0 < rank(25.0) < 1.0
    assert rank(None) == 0.0  # 결측 → 최하


def test_percentile_ranker_small_sample():
    rank = scoring.percentile_ranker([5.0])
    assert rank(5.0) == 0.5  # 소표본은 중립
    assert rank(None) == 0.0


def test_growth_score_ranks_high_growth_above_low():
    # 절대 밴드(종목분석과 동일): 고YoY 가 저YoY 보다 높은 절대 점수.
    high = screener._growth_score(_U(), _G(0.6, 0.6, False))
    low = screener._growth_score(_U(), _G(0.0, 0.0, False))
    assert high is not None and low is not None and high > low
    assert 0 <= low <= 100 and 0 <= high <= 100


def test_growth_score_turnaround_boosts():
    # 흑자전환 가점(같은 YoY 라도 더 높음).
    g_turn = _G(0.2, 0.2, True)
    g_plain = _G(0.2, 0.2, False)
    assert screener._growth_score(_U(), g_turn) > screener._growth_score(_U(), g_plain)


def test_growth_score_null_growth_none():
    # 성장지표 없는 종목(g=None) → 점수 계산 불가(None).
    assert screener._growth_score(_U(), None) is None


# ── 가치 전략 ──────────────────────────────────────────────────────────
def test_cheap_ranker_lower_is_higher():
    # 저평가 백분위: 값이 작을수록 1.0(PER/PBR 처럼 낮을수록 좋은 지표).
    rank = scoring.cheap_ranker([5.0, 10.0, 20.0, 40.0])
    assert rank(5.0) == 1.0  # 최저 → 최고 점수
    assert rank(40.0) == 0.0  # 최고 → 최저 점수
    assert rank(None) == 0.0  # 결측
    assert rank(-3.0) == 0.0  # 음수(적자 PER 등) → 최하


def test_value_score_cheap_above_expensive():
    # 절대 밴드(종목분석과 동일): 저평가가 고평가보다 높은 절대 점수.
    cheap = screener._value_score(_F(per=3.0, pbr=0.3, roe=15.0, ev_ebitda=3.0))
    pricey = screener._value_score(_F(per=30.0, pbr=3.0, roe=2.0, ev_ebitda=20.0))
    assert cheap is not None and pricey is not None and cheap > pricey
    assert 0 <= pricey <= 100 and 0 <= cheap <= 100


def test_value_score_none_is_none():
    # 재무 없음 → 점수 계산 불가(None). (구 백분위 방식의 0.0 과 달리 명시적 결측)
    assert screener._value_score(None) is None


def test_value_score_roe_bonus():
    # ROE 가 높으면 가점(같은 밸류 배수라도).
    hi = screener._value_score(_F(per=10.0, pbr=10.0, roe=15.0))
    lo = screener._value_score(_F(per=10.0, pbr=10.0, roe=0.0))
    assert hi > lo


def test_value_score_dividend_bonus():
    # 시가배당률이 높으면 가점.
    hi = screener._value_score(_F(per=10.0, pbr=10.0, div_yield=5.0))
    lo = screener._value_score(_F(per=10.0, pbr=10.0, div_yield=0.0))
    assert hi > lo


def test_screener_value_matches_company_analysis():
    # 회귀: 스크리너 가치 점수 = 종목분석 가치 점수(둘 다 절대 밴드 value_score_abs).
    from app.domain import analysis_scoring
    fin = _F(per=8.0, pbr=1.2, roe=12.0, ev_ebitda=6.0, div_yield=3.0)
    screener_v = screener._value_score(fin)
    analysis_v, _ = analysis_scoring.value_score_abs(
        fin.per, fin.pbr, fin.ev_ebitda, fin.roe, fin.div_yield
    )
    assert screener_v == analysis_v


# ── 거래대금 필터 완화 (배치 결측 회귀 가드) ────────────────────────────


@pytest.fixture
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import GrowthMetric, UniverseSnapshot

    eng = create_engine("sqlite://")
    Base.metadata.create_all(
        eng, tables=[UniverseSnapshot.__table__, GrowthMetric.__table__]
    )
    # 이벤트 컬럼 조회는 이 테스트 관심사(유니버스 필터) 밖이고, Broadcast 의 JSONB 가 sqlite 에서
    # 컴파일 안 되므로 비운다 — 필터 조건(어느 종목이 유니버스에 드는가)만 검증한다.
    monkeypatch.setattr(screener, "_representative_events", lambda *a, **k: {})
    monkeypatch.setattr(screener, "_coverage_subquery", _empty_coverage_subquery)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _empty_coverage_subquery(since):
    # Report/ReportAnalysis 조인 없이 빈 커버리지 서브쿼리(스키마 최소화).
    from sqlalchemy import literal, select

    return select(
        literal(None).label("stock_code"),
        literal(0).label("coverage_count"),
        literal(0).label("buy_count"),
    ).where(literal(False)).subquery()


def _snap(code, name, *, close, tv, mcap=10_000_000_000, stype="stock"):
    from app.db.models import UniverseSnapshot

    return UniverseSnapshot(
        snapshot_date=date(2026, 7, 15), stock_code=code, market="KOSPI",
        stock_name=name, stock_type=stype, close_price=close, change_pct=0.0,
        market_cap=mcap, trading_value=tv,
    )


def _codes(db, **kw):
    # include_etf=True 로 우선주 제외 regex(postgres `~` 전용, sqlite 미지원)를 우회한다.
    # 테스트 데이터는 모두 stock_type='stock' 이라 유니버스 결과는 동일하다.
    params = {
        "strategy": "growth", "mktcap_max": None, "mktcap_min": None, "liq_min": 100_000_000,
        "rev_yoy_min": None, "op_growth": None, "mom_min": None, "mom_max": None,
        "per_max": None, "pbr_max": None, "roe_min": None, "div_min": None, "market": None,
        "sector": None, "include_etf": True, "coverage": None, "recent_buy": False,
        "sort": "market_cap", "limit": 200, "offset": 0,
    }
    params.update(kw)
    return {r.stock_code for r in screener.screen(db, **params).items}


def test_relax_filter_includes_missing_tv_excludes_delisted(db):
    db.add_all([
        _snap("000001", "거래대금정상", close=10000, tv=500_000_000),  # 통과(tv>=1억)
        _snap("000002", "거래대금결측", close=10000, tv=None),  # 통과(결측 → close 로 구제)
        _snap("000003", "거래대금0", close=10000, tv=0),  # 통과(0 → 결측 취급, close 있음)
        _snap("000004", "거래정지", close=None, tv=None),  # 제외(close 없음=상장/거래 아님)
        _snap("000005", "저유동", close=10000, tv=50_000_000),  # 제외(tv 있는데 1억 미만)
    ])
    db.commit()
    codes = _codes(db, liq_min=100_000_000)
    assert "000002" in codes and "000003" in codes  # 결측/0 정상주 구제
    assert "000001" in codes  # 유동성 충족
    assert "000004" not in codes  # 거래정지(close 없음) 제외
    assert "000005" not in codes  # 유동성 하한 미달(값 있으면 하한 적용)


def test_liq_min_none_keeps_all_listed(db):
    # 유동성 필터 없으면(liq_min=None) 상장주(close 있음)는 tv 값과 무관하게 모두 포함.
    db.add_all([
        _snap("000001", "유동많음", close=10000, tv=500_000_000),
        _snap("000002", "결측", close=10000, tv=None),
        _snap("000003", "저유동", close=10000, tv=1_000),
    ])
    db.commit()
    codes = _codes(db, liq_min=None)
    assert codes == {"000001", "000002", "000003"}
