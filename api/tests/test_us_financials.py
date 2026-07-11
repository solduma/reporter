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


def test_discrete_quarters_dedup_and_span(nvda):
    # 매출 분기값: (start,end) 중복 접기 + ~90일 span 만. 최소 4개 이상이어야 TTM 가능.
    qs = us_financials._discrete_quarters(nvda, "Revenues")
    assert len(qs) >= 4
    ends = [e for e, _ in qs]
    assert ends == sorted(ends)  # end 오름차순
    assert len(ends) == len(set(ends))  # 중복 제거됨


def test_ttm_revenue_reasonable(nvda):
    # NVDA 최근 4분기 매출 합은 수백억 달러 규모(2025~2026 급성장).
    ttm = us_financials._ttm_revenue(nvda)
    assert ttm is not None
    assert ttm > 100e9  # 1000억 달러 초과


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
