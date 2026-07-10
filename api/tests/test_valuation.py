"""EV/EBITDA·PSR 산출 단위 테스트 — DART 계정 파싱 + TTM 계산(외부·DB 미접속)."""

from __future__ import annotations

from app.services import dart, valuation_ingest


def _row(sj, nm, amt):
    return {"sj_div": sj, "account_nm": nm, "thstrm_amount": amt}


# ── DART 계정 파싱 (_parse_statement) ──────────────────────────

def test_parse_operating_income_with_loss_suffix():
    # 계정명이 '영업이익(손실)' 이어도 잡아야 한다.
    rows = [_row("CIS", "영업이익(손실)", "8,865,562,046")]
    st = dart._parse_statement(rows)
    assert st.operating_income == 8865562046.0


def test_parse_depreciation_from_cf_only_not_bs_accumulated():
    # CF 감가상각비·무형자산상각비만 합산. BS '감가상각누계액'(잔액)은 제외.
    rows = [
        _row("IS", "영업이익", "100"),
        _row("CF", "감가상각비에 대한 조정", "60"),
        _row("CF", "무형자산상각비에 대한 조정", "10"),
        _row("BS", "감가상각누계액, 건물", "9999"),  # 잔액 — 제외돼야
        _row("CF", "대손상각비 조정", "5"),  # 대손 — 제외돼야
    ]
    st = dart._parse_statement(rows)
    assert st.depreciation == 70.0  # 60+10, 누계액·대손 제외
    assert st.ebitda == 170.0  # op 100 + dep 70


def test_parse_net_debt_borrowings_minus_cash():
    rows = [
        _row("BS", "단기차입금", "300"),
        _row("BS", "장기차입금", "200"),
        _row("BS", "사채", "100"),
        _row("BS", "현금및현금성자산", "400"),
    ]
    st = dart._parse_statement(rows)
    assert st.borrowings == 600.0
    assert st.net_debt == 200.0  # 600 - 400


def test_ebitda_none_without_operating_income():
    st = dart._parse_statement([_row("CF", "감가상각비에 대한 조정", "50")])
    assert st.ebitda is None  # 영업이익 없으면 EBITDA 없음


# ── period 파싱 + TTM ──────────────────────────

def test_period_to_year_q():
    assert valuation_ingest._period_to_year_q("2026.03") == (2026, 1)
    assert valuation_ingest._period_to_year_q("2025.12") == (2025, 4)
    assert valuation_ingest._period_to_year_q("2026.06(E)") == (2026, 2)
    assert valuation_ingest._period_to_year_q("2026.05") is None  # 분기말 아님
    assert valuation_ingest._period_to_year_q("연간") is None


def test_ttm_sum():
    assert valuation_ingest._ttm_sum([10, 20, 30, 40]) == 100
    assert valuation_ingest._ttm_sum([10, 20, 30]) is None  # 4개 미만
    assert valuation_ingest._ttm_sum([10, None, 30, 40]) is None  # 결측
