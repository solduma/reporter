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
def test_dcf_two_stage_moderate_growth():
    # 완만성장(10%, 영구 2% → 격차 8%p, 3단계 임계 미만) → 2단계 유지.
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.10, years=5, terminal_growth=0.02,
        discount_rate=0.10, net_debt=0, shares=1e8, current_price=None,
    )
    assert r.applicable and r.target_price and r.target_price > 0
    assert r.assumptions["stages"] == 2  # 완만성장 → 2단계
    assert r.confidence == "하"  # DCF 기본 신뢰도 낮음


def test_dcf_three_stage_for_high_growth():
    # 고성장(30%, 영구 2% → 격차 28%p > 8%) + roe·moat → 3단계(CAP 유지+감쇠).
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.30, years=5, terminal_growth=0.02,
        discount_rate=0.10, net_debt=0, shares=1e8, current_price=None,
        roe=0.25, moat="강",
    )
    assert r.applicable
    assert r.assumptions["stages"] == 3
    assert r.assumptions["plateau_years"] >= 1 and r.assumptions["fade_years"] >= 1
    assert any("선형 감쇠" in s for s in r.process)


def test_dcf_terminal_growth_used_as_is():
    # 영구성장률은 상한 없이 입력값 그대로 사용(실측 국고채 10년 기반). 할인율 미만이면 유효.
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.10, years=5, terminal_growth=0.04,
        discount_rate=0.10, net_debt=0, shares=1e8, current_price=None, risk_free=0.03,
    )
    assert r.applicable
    assert r.assumptions["growth_long"] == 0.04  # 캡 없이 그대로


def test_dcf_rejects_discount_le_terminal():
    # 할인율 3% ≤ 영구성장 4% → 고든 잔존가치 발산 방어(유일하게 남은 가드).
    r = v.dcf_valuation(
        fcf_base=100, growth_rate=0.03, years=5, terminal_growth=0.04,
        discount_rate=0.03, net_debt=0, shares=1e8, current_price=None,
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
    c = v.pbr_valuation(bps=9500, target_pbr=1.0, current_price=9000)  # 9500
    d = v.per_valuation(forward_eps=1000, target_per=100, current_price=9000)  # 100000 (폭주)
    for r in (a, b, c, d):
        r.confidence = "중"
    s = v.blend([a, b, c, d], current_price=9000)
    # d 는 이상치로 제외 → 평균은 (10000+11000+9500)/3 = 10166.67 → 반올림
    assert s.method_count == 4  # 결과에는 남지만
    assert "이상치" in d.note
    assert 9000 < s.final_target < 12000  # 폭주값 100000 이 최종에 안 섞임


def test_blend_growth_keeps_upside_method():
    # 성장주: 성장 반영 방식(높은 목표가)이 후행 앵커 클러스터 대비 높아도 이상치로 안 잘린다(상방 컷 완화).
    a = v.per_valuation(forward_eps=1000, target_per=10, current_price=9000)  # 10000
    b = v.pbr_valuation(bps=10000, target_pbr=1.0, current_price=9000)  # 10000
    c = v.pbr_valuation(bps=9500, target_pbr=1.0, current_price=9000)  # 9500
    up = v.per_valuation(forward_eps=1000, target_per=20, current_price=9000)  # 20000 (중앙값 대비 +100%)
    for r in (a, b, c, up):
        r.confidence = "중"
    # 일반: +60% 초과라 상방 방식 제외 → 최종 낮음.
    s_plain = v.blend([a, b, c, up], current_price=9000, is_growth=False)
    assert "이상치" in up.note
    up.note = ""  # 재사용 위해 초기화
    # 성장주: 상방 컷 +120% 라 20000(+100%)은 유지 → 최종이 더 높다.
    s_growth = v.blend([a, b, c, up], current_price=9000, is_growth=True)
    assert "이상치" not in up.note
    assert s_growth.final_target > s_plain.final_target


def test_blend_growth_still_cuts_downside_outlier():
    # 성장주라도 하방 이상치(-60% 초과)는 그대로 제외(상방만 완화).
    a = v.per_valuation(forward_eps=1000, target_per=10, current_price=9000)  # 10000
    b = v.pbr_valuation(bps=10000, target_pbr=1.0, current_price=9000)  # 10000
    low = v.pbr_valuation(bps=3000, target_pbr=1.0, current_price=9000)  # 3000 (-70%)
    for r in (a, b, low):
        r.confidence = "중"
    v.blend([a, b, low], current_price=9000, is_growth=True)
    assert "이상치" in low.note


def test_blend_empty_when_none_applicable():
    bad = v.per_valuation(forward_eps=None, target_per=None, current_price=5000)
    s = v.blend([bad], current_price=5000)
    assert s.final_target is None and s.method_count == 0


# ── 종목 유형별 방식 적합도(method_fit) + blend 제외 ────────────────────────
def test_method_fit_financial_excludes_ev_and_dcf():
    # 금융주(유의미 배당 3%): EV/EBITDA·FCFF DCF 제외(부채=원재료). DDM·PBR 우대.
    f = v.method_fit("financial", div_yield_pct=3.0)
    assert f["ev_ebitda"] == 0.0 and f["dcf"] == 0.0
    assert f["ddm"] > 1.0 and f["pbr"] > 1.0


def test_method_fit_growth_downweights_book_methods():
    # 성장주: PBR 저가중(장부가 ≪ 실제가치), PER·DCF 우대.
    f = v.method_fit("growth", div_yield_pct=2.0)
    assert f["pbr"] < 1.0
    assert f["per"] > 1.0 and f["dcf"] > 1.0
    assert "asset" not in f  # 자산가치 방식 제거됨


def test_method_fit_dividend_and_loss_gates():
    # 무배당·미미배당 → DDM 제외. 유의미 배당(≥1.5%) → DDM 유지. 적자 → PER·DCF 제외.
    assert v.method_fit("growth")["ddm"] == 0.0  # 배당수익률 미지정(None) → 제외
    assert v.method_fit("growth", div_yield_pct=0.5)["ddm"] == 0.0  # 미미배당 → 제외
    assert v.method_fit("financial", div_yield_pct=4.0)["ddm"] > 0.0  # 유의미 배당 → 유지
    loss = v.method_fit("other", is_loss=True, div_yield_pct=3.0)
    assert loss["per"] == 0.0 and loss["dcf"] == 0.0


def test_method_fit_low_yield_excludes_ddm():
    # KINX형 회귀: 성장주 첫 미미배당(0.45%)은 DDM 제외 — 목표가 하단 왜곡 방지.
    assert v.method_fit("growth", div_yield_pct=0.45)["ddm"] == 0.0


def test_blend_excludes_unfit_methods():
    # 금융주: 부적합 EV/EBITDA·DCF 는 폭주값이어도 최종 평균 제외(적합 PER·DDM 만 반영).
    per = v.per_valuation(forward_eps=1000, target_per=10, current_price=9000)  # 10000
    ddm = v.ddm_valuation(dps=500, dividend_growth=0.02, cost_of_equity=0.08, current_price=9000)
    ev = v.ev_ebitda_valuation(forward_ebitda=100, target_ev_ebitda=50, net_debt=0,
                               shares=1e6, current_price=9000)  # 폭주
    for r in (per, ddm, ev):
        r.confidence = "중"
    s = v.blend([per, ddm, ev], 9000, v.method_fit("financial", div_yield_pct=3.0))
    assert "부적합" in ev.note  # 금융주에 EV/EBITDA 제외
    assert s.final_target and s.final_target < 20000  # 폭주값 안 섞임


def test_blend_outlier_median_not_polluted_by_unfit():
    # 회귀 방지: 부적합(fit=0) 방식의 폭주값이 이상치 중앙값을 오염시켜 적합 방식을 제외시키면 안 됨.
    per = v.per_valuation(forward_eps=1000, target_per=10, current_price=9000)  # 10000 적합
    ddm = v.ddm_valuation(dps=550, dividend_growth=0.02, cost_of_equity=0.08, current_price=9000)  # 적합
    ev = v.ev_ebitda_valuation(forward_ebitda=100, target_ev_ebitda=90, net_debt=0,
                               shares=1e6, current_price=9000)  # 부적합·초폭주
    dcf = v.dcf_valuation(fcf_base=100, growth_rate=0.0, years=1, terminal_growth=0.0,
                          discount_rate=0.10, net_debt=0, shares=1e6, current_price=9000)  # 부적합
    for r in (per, ddm, ev, dcf):
        r.confidence = "중"
    s = v.blend([per, ddm, ev, dcf], 9000, v.method_fit("financial", div_yield_pct=3.0))
    assert "부적합" not in per.note  # PER 은 적합 → 부적합 제외 안 됨
    assert "이상치" not in per.note and "이상치" not in ddm.note  # 적합 방식이 오제외되면 안 됨
    assert s.final_target and 9000 < s.final_target < 15000  # 폭주 EV 안 섞임
