"""재무 10년 백필 단위 테스트 — 분기환산(4Q=연간-누적)·TTM·분할무관 밸류 계산."""

from __future__ import annotations

from app.domain import financials
from app.services import financials_backfill as fb


def test_discrete_q1_to_q3_passthrough_q4_subtracts():
    # 1~3Q 는 당기값 그대로, 4Q=연간-(1Q+2Q+3Q). 백필은 도메인 규칙(discrete_quarter)을 공유.
    cum = {(2023, 1): 10.0, (2023, 2): 20.0, (2023, 3): 30.0, (2023, 4): 100.0}
    assert financials.discrete_quarter(cum, (2023, 1)) == 10.0
    assert financials.discrete_quarter(cum, (2023, 2)) == 20.0
    assert financials.discrete_quarter(cum, (2023, 3)) == 30.0
    assert financials.discrete_quarter(cum, (2023, 4)) == 40.0  # 100-(10+20+30)


def test_discrete_q4_missing_part_returns_none():
    # 4Q 환산에 1~3Q 중 하나라도 없으면 None(15개월 오인 방지).
    cum = {(2023, 1): 10.0, (2023, 3): 30.0, (2023, 4): 100.0}  # 2Q 결측
    assert financials.discrete_quarter(cum, (2023, 4)) is None


def test_ttm_sums_four_consecutive_quarters():
    # 이미 분기 개별 환산된 dict 를 합(_ttm_from_discrete) — 음수매출 필터 이후 단계.
    discrete = {(2023, 1): 1.0, (2023, 2): 2.0, (2023, 3): 3.0, (2023, 4): 4.0}
    assert fb._ttm_from_discrete(discrete, (2023, 4)) == 10.0
    # 하나라도 결측이면 None.
    assert fb._ttm_from_discrete(discrete, (2023, 3)) is None  # 2022 4Q 없음


def test_ttm_crosses_year_boundary():
    discrete = {(2022, 4): 4.0, (2023, 1): 1.0, (2023, 2): 2.0, (2023, 3): 3.0}
    assert fb._ttm_from_discrete(discrete, (2023, 3)) == 10.0  # 23Q3+23Q2+23Q1+22Q4


def test_period_str_maps_quarter_to_month():
    assert fb._period_str(2026, 1) == "2026.03"
    assert fb._period_str(2026, 4) == "2026.12"


def test_target_year_quarters_excludes_future():
    from datetime import date

    yqs = fb._target_year_quarters(date(2026, 7, 10))
    # 2026 3Q(9월말)·4Q 는 미래라 제외, 2026 2Q(6월말)까지 포함.
    assert (2026, 2) in yqs
    assert (2026, 3) not in yqs
    assert (2026, 4) not in yqs
    # 10년 전 시작.
    assert yqs[0][0] == 2016


def test_backfill_writes_operating_income(monkeypatch):
    # 회귀: financials_backfill 이 DART op_income 을 수집·분기환산·저장해야 한다(과거 분기 null 방지).
    from unittest.mock import MagicMock

    from app.adapters.dart.client import IncomeEquity

    captured = []
    monkeypatch.setattr(fb, "_upsert_financial", lambda db, code, period, **v: captured.append((period, v)))
    monkeypatch.setattr(fb, "_quarter_end_close", lambda *a, **k: None)
    monkeypatch.setattr(fb.quote, "fetch_shares_outstanding", lambda *a, **k: 1_000_000)
    # 4분기 누적 재무(op_income 포함). 분기환산은 도메인이 담당.
    cum = {
        (2024, 1): IncomeEquity(revenue=100e8, operating_income=10e8, net_income=8e8, eps=100, equity=500e8),
        (2024, 2): IncomeEquity(revenue=220e8, operating_income=24e8, net_income=18e8, eps=220, equity=510e8),
        (2024, 3): IncomeEquity(revenue=340e8, operating_income=39e8, net_income=30e8, eps=340, equity=520e8),
        (2024, 4): IncomeEquity(revenue=480e8, operating_income=56e8, net_income=45e8, eps=480, equity=530e8),
    }
    monkeypatch.setattr(
        fb.dart, "fetch_income_and_equity",
        lambda key, corp, year, q, sess: (cum.get((year, q)), None),
    )
    db = MagicMock()
    db.scalar.return_value = "00000000"  # corp_code
    settings = MagicMock()
    fb.backfill_stock(db, settings, "093320")

    # 저장된 분기 중 op_income 이 실린 것이 있어야 하고, 억원 단위(원/1e8)여야 한다.
    op_written = [(p, v["operating_income"]) for p, v in captured if v.get("operating_income") is not None]
    assert op_written, "operating_income 이 하나도 저장되지 않음(회귀)"
    # 2024.03 개별 op = 10억 → 억원 단위 10.0
    q1 = next((v for p, v in op_written if p == "2024.03"), None)
    assert q1 == 10.0
