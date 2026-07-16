"""다중 밸류에이션 순수 도메인 테스트 — 산식·경계·이상치 제외·blend 가중."""

from __future__ import annotations

from app.domain import valuation as v


# ── 상대가치 ────────────────────────────────────────────────────────────
def test_per_valuation_basic():
    r = v.per_valuation(forward_eps=5000, target_per=12, current_price=40000)
    assert r.applicable
    assert r.target_price == 60000  # 5000 × 12
    assert r.upside_pct == 50.0  # (60000-40000)/40000
    assert any("60,000" in s for s in r.process)


def test_per_rejects_negative_eps():
    r = v.per_valuation(forward_eps=-100, target_per=12, current_price=40000)
    assert not r.applicable and "적자" in r.note and r.target_price is None


def test_pbr_valuation_basic():
    r = v.pbr_valuation(bps=50000, target_pbr=0.8, current_price=30000)
    assert r.applicable
    assert r.target_price == 40000  # 50000 × 0.8
    assert r.upside_pct == round((40000 - 30000) / 30000 * 100, 1)


def test_pbr_rejects_capital_impairment():
    r = v.pbr_valuation(bps=-1000, target_pbr=1.0, current_price=5000)
    assert not r.applicable and "자본잠식" in r.note


def test_ev_ebitda_with_net_debt():
    # EBITDA 1000억 × 8 = EV 8000억, 순차입 2000억 → 지분 6000억 ÷ 1억주 = 6000원
    r = v.ev_ebitda_valuation(
        forward_ebitda=1000, target_ev_ebitda=8, net_debt=2000, shares=1e8, current_price=5000
    )
    assert r.applicable
    assert r.target_price == 6000
    assert r.upside_pct == 20.0


def test_ev_ebitda_net_cash_increases_value():
    # 순현금(음수 순차입) → 지분가치가 EV 보다 큼
    r = v.ev_ebitda_valuation(
        forward_ebitda=1000, target_ev_ebitda=8, net_debt=-1000, shares=1e8, current_price=5000
    )
    assert r.target_price == 9000  # (8000 - (-1000)) 억 / 1억주


# ── 절대가치 ────────────────────────────────────────────────────────────
def test_dcf_two_stage():
    # 결정론적: FCF 100억, 10% 성장 5년, 영구성장 2%, 할인율 10%, 순차입 0, 1억주
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.10, years=5, terminal_growth=0.02,
        discount_rate=0.10, net_debt=0, shares=1e8, current_price=None,
    )
    assert r.applicable and r.target_price and r.target_price > 0
    # 수기 검산: 명시적 5년 현가 + 잔존 현가 → 지분가치 계산이 프로세스에 드러남
    assert len(r.process) == 5
    assert r.confidence == "하"  # DCF 기본 신뢰도 낮음


def test_dcf_rejects_discount_le_terminal():
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.05, years=5, terminal_growth=0.05,
        discount_rate=0.05, net_debt=0, shares=1e8, current_price=None,
    )
    assert not r.applicable and "발산" in r.note


def test_dcf_value_matches_hand_calc():
    # 성장 0·영구성장 0 → 명시적 구간 = FCF/(1.1^t) 합, 잔존 = FCF/0.1 할인.
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.0, years=1, terminal_growth=0.0,
        discount_rate=0.10, net_debt=0, shares=1e8, current_price=None,
    )
    # t=1: FCF=100, PV=100/1.1=90.909; terminal=100/0.1=1000, PV=1000/1.1=909.09
    # EV=1000억, 지분 1000억/1억주 = 1000원
    assert r.target_price == 1000


def test_ddm_gordon():
    # DPS 1000, 성장 3%, 자본비용 8% → D1=1030, 1030/0.05 = 20600
    r = v.ddm_valuation(dps=1000, dividend_growth=0.03, cost_of_equity=0.08, current_price=15000)
    assert r.applicable
    assert r.target_price == 20600


def test_ddm_rejects_no_dividend():
    r = v.ddm_valuation(dps=0, dividend_growth=0.03, cost_of_equity=0.08, current_price=15000)
    assert not r.applicable and "무배당" in r.note


def test_asset_liquidation_discount():
    r = v.asset_valuation(book_equity_per_share=10000, asset_premium=0.7, current_price=5000)
    assert r.applicable and r.target_price == 7000
    assert "청산할인" in r.process[1]


def test_asset_revaluation_premium():
    r = v.asset_valuation(book_equity_per_share=10000, asset_premium=1.3, current_price=5000)
    assert r.target_price == 13000 and "재평가할증" in r.process[1]


# ── 요인모형 ────────────────────────────────────────────────────────────
def test_fama_french_required_return_and_target():
    # rf 3% + 시장(β1×6%) + SMB(β0.3×2%) + HML(β0.5×3%) = 3+6+0.6+1.5 = 11.1%
    # 목표PER = 1/(0.111-0.05) = 16.39배, EPS 1000 → 16393원
    factors = [
        v.FactorExposure("시장", 1.0, 0.06),
        v.FactorExposure("SMB(규모)", 0.3, 0.02),
        v.FactorExposure("HML(가치)", 0.5, 0.03),
    ]
    r = v.fama_french_valuation(
        forward_eps=1000, risk_free=0.03, factors=factors,
        earnings_growth=0.05, current_price=12000,
    )
    assert r.applicable
    assert abs(r.assumptions["required_return"] - 0.111) < 1e-9
    assert r.target_price == round(1000 / (0.111 - 0.05))


def test_apt_rejects_required_le_growth():
    factors = [v.FactorExposure("금리", 0.1, 0.01)]
    r = v.apt_valuation(
        forward_eps=1000, risk_free=0.03, factors=factors,
        earnings_growth=0.10, current_price=12000,  # 요구수익률 ~3.1% < 성장 10%
    )
    assert not r.applicable and "발산" in r.note


# ── blend(최종 목표가) ───────────────────────────────────────────────────
def test_blend_confidence_weighted():
    per = v.per_valuation(forward_eps=5000, target_per=12, current_price=40000)  # 60000, 중
    pbr = v.pbr_valuation(bps=50000, target_pbr=0.8, current_price=40000)  # 40000, 중
    per.confidence = "상"  # 가중 3
    pbr.confidence = "중"  # 가중 2
    s = v.blend([per, pbr], current_price=40000)
    # (60000×3 + 40000×2) / 5 = 52000
    assert s.final_target == 52000
    assert s.method_count == 2
    assert s.final_upside_pct == 30.0


def test_blend_excludes_outlier():
    # 정상 3개(약 10000 근처) + 폭주 1개(100000) → 이상치 제외
    a = v.per_valuation(forward_eps=1000, target_per=10, current_price=9000)  # 10000
    b = v.pbr_valuation(bps=11000, target_pbr=1.0, current_price=9000)  # 11000
    c = v.asset_valuation(book_equity_per_share=9500, asset_premium=1.0, current_price=9000)  # 9500
    d = v.per_valuation(forward_eps=1000, target_per=100, current_price=9000)  # 100000 (폭주)
    for r in (a, b, c, d):
        r.confidence = "중"
    s = v.blend([a, b, c, d], current_price=9000)
    # d 는 이상치로 제외 → 평균은 (10000+11000+9500)/3 = 10166.67 → 반올림
    assert s.method_count == 4  # 결과에는 남지만
    assert "이상치" in d.note
    assert 9000 < s.final_target < 12000  # 폭주값 100000 이 최종에 안 섞임


def test_blend_empty_when_none_applicable():
    bad = v.per_valuation(forward_eps=None, target_per=None, current_price=5000)
    s = v.blend([bad], current_price=5000)
    assert s.final_target is None and s.method_count == 0
