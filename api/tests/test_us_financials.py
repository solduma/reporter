"""US 재무 도메인 계산 단위 테스트 — SEC companyfacts 픽스처(NVDA)로 순수 로직 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain import us_financials

_FIXTURE = Path(__file__).parent / "fixtures" / "sec_nvda_facts.json"


@pytest.fixture
def nvda() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_ttm_uses_fy_plus_delta_not_naive_4q(nvda):
    # 회귀(중요): 10-K 는 discrete Q4 를 안 줘서 '최근 4개 ~90일 분기 무검증 합산'은 틀린다.
    # 올바른 TTM = 최근 FY(215.9B, ~2026-01 종료) + Q1'26(81.6B) - Q1'25(44.1B) = 253.5B.
    # 순진한 4분기 합(229.4B)이 아니어야 한다.
    ttm = us_financials._ttm_revenue(nvda)
    assert ttm is not None
    assert abs(ttm - 253.5e9) < 1e9  # FY+delta 방식
    assert abs(ttm - 229.4e9) > 5e9  # 순진한 4분기 합이 아님


def test_ttm_periods_dedup(nvda):
    # (start,end) 중복 제거되어 하나의 값만.
    ps = us_financials._periods(nvda, "Revenues")
    keys = list(ps.keys())
    assert len(keys) == len(set(keys))


def test_revenue_picks_account_with_latest_data():
    # 회귀(MSFT형): 구 'Revenues'가 2010년까지만·현행은 RevenueFromContract 인 경우,
    # 첫 non-None 이 아니라 '최신 데이터 계정'을 골라야 한다.
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {  # 구 계정: 2010년까지만
                    "units": {"USD": [
                        {"start": "2009-01-01", "end": "2009-12-31", "val": 50e9},
                        {"start": "2010-01-01", "end": "2010-03-31", "val": 12e9},
                    ]}
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {  # 현행
                    "units": {"USD": [
                        {"start": "2024-01-01", "end": "2024-12-31", "val": 280e9},  # FY
                        {"start": "2024-01-01", "end": "2024-03-31", "val": 60e9},   # 전년 Q1
                        {"start": "2025-01-01", "end": "2025-03-31", "val": 70e9},   # 당해 Q1
                    ]}
                },
            }
        }
    }
    ttm = us_financials._ttm_revenue(facts)
    # 현행 계정: 280 + (70-60) = 290B. 구 계정(2009 FY 50B)이 아님.
    assert ttm is not None
    assert abs(ttm - 290e9) < 1e9


def test_latest_shares_and_equity(nvda):
    assert us_financials._latest_shares(nvda) > 20e9  # ~240억 주
    assert us_financials._latest_instant(nvda, "StockholdersEquity") > 0


def test_compute_metrics_with_market_cap(nvda):
    # 시총 4조 달러 가정 → PER/PBR/PSR/ROE 산출되고 양수.
    r = us_financials.compute(nvda, market_cap=4_000e9)
    assert r.per and r.per > 0
    assert r.pbr and r.pbr > 0
    assert r.psr and r.psr > 0
    assert r.roe and r.roe > 0
    assert r.ttm_revenue and r.ttm_net_income


def test_compute_without_market_cap_yields_no_multiples(nvda):
    # 시총 없으면 배수는 None, TTM 원자료는 채워짐.
    r = us_financials.compute(nvda, market_cap=None)
    assert r.per is None and r.pbr is None and r.psr is None
    assert r.ttm_revenue is not None
    # ROE 는 시총 무관(순이익/자본)이라 산출 가능.
    assert r.roe is not None


def test_compute_empty_facts_all_none():
    r = us_financials.compute({"facts": {"us-gaap": {}}}, market_cap=1e12)
    assert r.per is None and r.pbr is None and r.roe is None
    assert r.ttm_revenue is None
