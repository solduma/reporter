"""재무 10년 백필 단위 테스트 — 분기환산(4Q=연간-누적)·TTM·분할무관 밸류 계산."""

from __future__ import annotations

from app.adapters.dart.client import _parse_income_equity
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
    # 새 파이프라인(FS 원문 우선): fetch_full_statements_by_div 가 원문을 주고
    # parse_income_equity_from_fs 가 IncomeEquity 로 환원한다. 파서 매핑은 목킹.
    current: dict = {}

    def _fake_fetch(key, corp, year, q, fs_div, sess):
        current["yq"] = (year, q)
        return {"IS": []}

    monkeypatch.setattr(fb.dart, "fetch_full_statements_by_div", _fake_fetch)
    monkeypatch.setattr(fb, "parse_income_equity_from_fs", lambda fs_data: cum.get(current["yq"]))
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


def test_securities_cis_revenue_sums_components():
    # 회귀: 증권사(CIS 기반)는 단일 매출액이 없어 구성요소(수수료·이자·기타영업수익)를
    # 합산해야 한다. SK증권 값: 708+429+215 = 1,352억.
    rows = [
        {"account_id": "ifrs-full_FeeAndCommissionIncome", "account_nm": "수수료수익", "sj_div": "CIS", "thstrm_amount": "70800000000"},
        {"account_id": "ifrs-full_RevenueFromInterest", "account_nm": "이자수익", "sj_div": "CIS", "thstrm_amount": "42900000000"},
        {"account_id": "dart_OtherOperatingIncome", "account_nm": "기타영업수익", "sj_div": "CIS", "thstrm_amount": "21500000000"},
    ]
    fin = _parse_income_equity(rows)
    assert fin.revenue == 1352e8


def test_is_based_company_revenue_not_summed_with_interest():
    # 회귀: 일반 제조업(IS 기반)의 이자수익은 매출에 섞이면 안 된다. 매출 1,000억만.
    rows = [
        {"account_id": "ifrs-full_Revenue", "account_nm": "매출액", "sj_div": "IS", "thstrm_amount": "100000000000"},
        {"account_id": "ifrs-full_RevenueFromInterest", "account_nm": "이자수익", "sj_div": "IS", "thstrm_amount": "5000000000"},
    ]
    fin = _parse_income_equity(rows)
    assert fin.revenue == 1000e8


def test_insurance_cis_revenue_single_line():
    # 회귀: 보험사는 단일 보험수익 항목 — 합산 없이 그대로.
    rows = [
        {"account_id": "ifrs-full_InsuranceRevenue", "account_nm": "보험수익", "sj_div": "CIS", "thstrm_amount": "150000000000"},
    ]
    fin = _parse_income_equity(rows)
    assert fin.revenue == 1500e8


def test_backfill_stock_handles_existing_fs_rows(monkeypatch):
    """회귀: existing_fs 로딩이 Row 가 아닌 엔티티를 다뤄야 한다.

    select(FinancialStatement) 는 Row 키가 엔티티명 하나뿐이라 r.period 접근이
    AttributeError 난다(08-12 재무 10년 백필 97.5% 정지 원인). db.scalars 로
    엔티티를 직접 받아야 FS 행이 있는 종목도 크래시 없이 처리된다.
    """
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, CorpCodeMap, FinancialStatement

    # SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON 으로 렌더.
    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(type_, compiler, **kw):
        return "JSON"

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[CorpCodeMap.__table__, FinancialStatement.__table__]
    )
    db = sessionmaker(bind=engine)()
    db.add(CorpCodeMap(stock_code="000001", corp_code="00123456", corp_name="테스트"))
    db.add(FinancialStatement(
        stock_code="000001", period="2023.09", fs_div="CFS", data={"IS": []}
    ))
    db.commit()

    # DART·주식수 호출은 모킹 — FS 원문 우선 경로(existing_fs)만 검증.
    monkeypatch.setattr(fb.dart, "fetch_full_statements_by_div", lambda *a, **k: None)
    monkeypatch.setattr(fb.quote, "fetch_shares_outstanding", lambda *a, **k: None)
    try:
        assert fb.backfill_stock(db, MagicMock(), "000001") is True
    finally:
        db.close()
