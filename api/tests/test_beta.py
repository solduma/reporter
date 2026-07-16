"""요인 베타 순수 도메인 테스트 — 시장베타 회귀·SMB/HML 프록시."""

from __future__ import annotations

import math

from app.domain import beta


def _series(closes, start="2025-01-01"):
    # (날짜iso, 종가) — 날짜는 일련번호로 충분(정합만 되면 됨).
    return [(f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", c) for i, c in enumerate(closes)]


def test_market_beta_perfectly_correlated_is_one():
    # 주식 = 지수 × 상수(같은 수익률) → 베타 1.
    idx = [100 * (1.01 ** i) for i in range(80)]
    stock = [50 * (1.01 ** i) for i in range(80)]  # 동일 수익률, 다른 레벨
    b = beta.market_beta(_series(stock), _series(idx))
    assert b is not None and abs(b - 1.0) < 1e-6


def test_market_beta_double_amplitude_is_two():
    # 주식 수익률이 지수의 2배 → 베타 2.
    idx_ret = [0.01, -0.02, 0.03, -0.01, 0.02] * 16
    stock_ret = [r * 2 for r in idx_ret]
    idx, stock = [100.0], [100.0]
    for r in idx_ret:
        idx.append(idx[-1] * (1 + r))
    for r in stock_ret:
        stock.append(stock[-1] * math.exp(math.log(1 + r) if 1 + r > 0 else 0))
    # 로그수익률 기준 2배가 되도록 stock 을 지수의 제곱비로 구성
    stock = [100.0 * (idx[i] / 100.0) ** 2 for i in range(len(idx))]
    b = beta.market_beta(_series(stock), _series(idx))
    assert b is not None and abs(b - 2.0) < 0.05


def test_market_beta_none_when_insufficient_points():
    idx = [100 + i for i in range(10)]
    stock = [50 + i for i in range(10)]
    assert beta.market_beta(_series(stock), _series(idx), min_points=60) is None


def test_market_beta_none_when_no_common_dates():
    stock = [("2025-01-01", 100), ("2025-01-02", 101)]
    index = [("2024-01-01", 200), ("2024-01-02", 201)]
    assert beta.market_beta(stock, index, min_points=1) is None


def test_market_beta_clamped():
    # 극단 변동(저유동성) → -1~3 클램프.
    idx = [100 * (1.001 ** i) for i in range(80)]
    stock = [50 * (1.05 ** i) for i in range(80)]  # 지수 대비 훨씬 큰 추세 → 큰 베타
    b = beta.market_beta(_series(stock), _series(idx))
    assert b is not None and -1.0 <= b <= 3.0


def test_smb_beta_small_cap_positive_large_negative():
    assert beta.smb_beta(300) > 0.5  # 300억 소형 → 높은 SMB
    assert beta.smb_beta(500_000) < 0  # 50조 대형 → 음의 SMB
    assert beta.smb_beta(None) == 0.0


def test_hml_beta_value_vs_growth():
    assert beta.hml_beta(0.4) > 0.5  # 저PBR 가치주 → 높은 HML
    assert beta.hml_beta(3.0) < 0  # 고PBR 성장주 → 음의 HML
    assert beta.hml_beta(1.0) == 0.0  # PBR 1 기준점
    assert beta.hml_beta(None) == 0.0


def test_premiums_are_sane_constants():
    assert 0.02 <= beta.RISK_FREE <= 0.05
    assert 0.04 <= beta.MARKET_PREMIUM <= 0.08


# ── H-Model 감쇠기간(임의 상하한 없이 초과수익·WACC 에서 유도) ────────────
def test_fade_years_zero_when_no_excess_return():
    # 초과수익 0(ROE=WACC) 또는 가치파괴(ROE<WACC) → 감쇠 0년(하한 상수 없이 자연 수렴).
    assert beta.fade_years(0.08, 0.08, "강")[0] == 0.0  # ROE=WACC
    assert beta.fade_years(0.05, 0.09, "강")[0] == 0.0  # ROE<WACC(가치파괴)


def test_fade_years_bounded_by_inverse_wacc():
    # 자연 상한 = 1/WACC. 감쇠기간이 이 지평을 넘지 않는다(상수 상한 아님).
    for roe, w in [(0.50, 0.08), (0.30, 0.10), (0.99, 0.06)]:
        y, _ = beta.fade_years(roe, w, "강")
        assert y <= 1.0 / w + 0.1  # +0.1: round(,1) 반올림 여유


def test_fade_years_lower_wacc_allows_longer_horizon():
    # 같은 초과수익폭이라도 WACC 낮으면(할인 지평 김) 감쇠기간 길다.
    lo_wacc, _ = beta.fade_years(0.16, 0.06, "강")  # spread 10%p, 지평 16.7년
    hi_wacc, _ = beta.fade_years(0.19, 0.09, "강")  # spread 10%p, 지평 11.1년
    assert lo_wacc > hi_wacc


def test_fade_years_moat_multiplier():
    # 같은 ROE·WACC 라도 해자 등급이 지속성을 조정(강>중>약).
    strong, _ = beta.fade_years(0.20, 0.079, "강")
    mid, _ = beta.fade_years(0.20, 0.079, "중")
    weak, _ = beta.fade_years(0.20, 0.079, "약")
    assert strong > mid > weak


def test_fade_years_accepts_percent_roe():
    # ROE 가 % 단위(20.0)로 들어와도 소수(0.20)와 동일 결과.
    assert beta.fade_years(20.0, 0.079, "중")[0] == beta.fade_years(0.20, 0.079, "중")[0]


def test_fade_years_zero_when_roe_or_wacc_missing():
    # 초과수익 미확인 → 보수적으로 0년(성장 프리미엄 없음).
    assert beta.fade_years(None, 0.08, "강")[0] == 0.0
    assert beta.fade_years(0.20, None, "강")[0] == 0.0
