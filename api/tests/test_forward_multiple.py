"""성장반영 3단계 forward 멀티플(growth_forward_multiple) — 대수 검산·가드·g≤ROE 캡."""

from __future__ import annotations

from app.domain.forward import growth_forward_multiple as gfm


def _direct_p0_over_e1(g1, gn, r, n, roe_hi, roe_st, fade):
    """정의식 직접 계산(P0/E1) — 3단계(고성장 n · fade fade · terminal) 배당할인. 검산 기준."""
    eps = 1.0
    disc = 1.0
    pv = 0.0
    for _ in range(n):  # 1단계
        eps *= 1 + g1
        disc *= 1 + r
        pv += max(0.0, 1 - g1 / roe_hi) * eps / disc
    for j in range(1, fade + 1):  # 2단계 fade
        g = g1 + (gn - g1) * j / fade
        roe_f = roe_hi + (roe_st - roe_hi) * j / fade
        eps *= 1 + g
        disc *= 1 + r
        pv += (max(0.0, 1 - g / roe_f) if roe_f > 0 else 0.0) * eps / disc
    b_term = 1 - gn / roe_st  # 3단계
    pv += b_term * eps * (1 + gn) / (r - gn) / disc
    return pv / (1 + g1)


def test_matches_direct_definition():
    # 폐형식 구현이 정의식 직접 계산과 일치(fade·terminal ROE→COE, n=CAP). g<ROE 케이스.
    g1, gn, r, n, roe = 0.08, 0.03, 0.09, 5, 0.12
    mult, meta = gfm(g1, roe, r, gn, n)
    direct = _direct_p0_over_e1(g1, gn, r, n, roe, r, n)  # terminal ROE=COE, fade=n
    assert mult is not None
    assert abs(mult - round(direct, 1)) < 0.15
    assert meta["source"] == "growth_3stage_forward"


def test_growth_capped_at_roe():
    # forward 성장이 ROE 초과면 g≤ROE 로 캡(재투자율≤1 항등식) — 발산 방지, meta 에 캡 고지.
    mult_hi, meta_hi = gfm(0.90, 0.092, 0.0626, 0.03, 5)  # forward 90% ≫ ROE 9.2%
    mult_cap, _ = gfm(0.092, 0.092, 0.0626, 0.03, 5)  # g=ROE 직접
    assert meta_hi["growth_capped"] is True
    assert meta_hi["raw_growth_pct"] == 90.0
    assert meta_hi["fwd_growth_pct"] == 9.2  # ROE 로 캡됨
    assert mult_hi == mult_cap  # 캡되어 동일


def test_no_divergence_high_growth():
    # 초고성장 forward 여도 g≤ROE 캡으로 폭발하지 않는다(현실적 ROE·CAP 에서 유계).
    # 과거 EBITDA 91% 외삽 같은 케이스: forward 90%, ROE 9.2%, COE 6.26%, CAP 5 → g=9.2% 로 캡.
    mult, meta = gfm(0.90, 0.092, 0.0626, 0.03, 5)
    assert mult is not None and 0 < mult < 50  # 캡 후 유계(596배 폭주 회피)
    assert meta["growth_capped"] is True


def test_terminal_growth_ge_coe_skips():
    # 영구성장률 ≥ COE 면 고든 발산 → 미산출(r−g>0 가드).
    mult, meta = gfm(0.10, 0.092, 0.0626, 0.07, 5)  # terminal 7% > COE 6.26%
    assert mult is None
    assert "영구성장률" in meta["reason"]


def test_missing_inputs_skip():
    assert gfm(None, 0.09, 0.06, 0.03, 5)[0] is None  # forward 결측
    assert gfm(0.1, None, 0.06, 0.03, 5)[0] is None  # ROE 결측
    assert gfm(0.1, 0.09, None, 0.03, 5)[0] is None  # COE 결측
    assert gfm(0.1, 0.09, 0.06, 0.03, None)[0] is None  # CAP 결측
    assert gfm(0.1, -0.05, 0.06, 0.03, 5)[0] is None  # ROE 음수


def test_no_growth_converges_below_commodity():
    # 무성장(g=0)·유한 CAP 이면 배수는 1/COE(commodity) 근처 또는 이하 — 영구 초과수익 가정 안 함.
    mult, _ = gfm(0.0, 0.08, 0.08, 0.0, 5)  # g=0, ROE=COE(초과수익 0), gn=0
    assert mult is not None
    assert mult <= round(1 / 0.08, 1) + 0.5  # 1/COE=12.5 이하 수준


def test_excess_return_raises_multiple():
    # 같은 성장이라도 ROE>COE(초과수익) 면 배수가 ROE=COE 대비 높다(프랜차이즈 프리미엄).
    high_roe, _ = gfm(0.05, 0.20, 0.08, 0.03, 5)  # ROE 20% ≫ COE 8%
    no_excess, _ = gfm(0.05, 0.08, 0.08, 0.03, 5)  # ROE=COE
    assert high_roe > no_excess
