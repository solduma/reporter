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


# ── 경쟁우위기간 CAP(해자 기준연수 × ROE 초과수익 지속성, 상한 12년) ────────
def test_cap_moat_base_years():
    # 같은 ROE·할인율이면 해자 등급이 기준연수를 정한다(강>중>약).
    strong, _ = beta.competitive_advantage_period(0.20, 0.08, "강")
    mid, _ = beta.competitive_advantage_period(0.20, 0.08, "중")
    weak, _ = beta.competitive_advantage_period(0.20, 0.08, "약")
    assert strong > mid > weak


def test_cap_higher_excess_return_longer():
    # 같은 해자라도 초과수익(ROE−할인율) 클수록 CAP 길다(지속성↑).
    hi, _ = beta.competitive_advantage_period(0.30, 0.08, "중")  # spread 22%p
    lo, _ = beta.competitive_advantage_period(0.10, 0.08, "중")  # spread 2%p
    assert hi > lo


def test_cap_bounded_by_max():
    # 극단 고ROE·저할인율이어도 상한 MAX_CAP_YEARS 를 넘지 않는다(1/WACC 폭증 방지).
    for roe, d in [(0.99, 0.05), (0.80, 0.06), (0.50, 0.07)]:
        y, _ = beta.competitive_advantage_period(roe, d, "강")
        assert y <= beta.MAX_CAP_YEARS


def test_cap_accepts_percent_roe():
    # ROE 가 % 단위(20.0)로 들어와도 소수(0.20)와 동일 결과.
    assert (
        beta.competitive_advantage_period(20.0, 0.08, "중")[0]
        == beta.competitive_advantage_period(0.20, 0.08, "중")[0]
    )


def test_cap_neutral_when_roe_or_discount_missing():
    # 초과수익 미확인 → 지속성 중립(해자 기준의 0.5배). 0 이 아니라 보수적 기준값.
    y, _ = beta.competitive_advantage_period(None, 0.08, "중")
    assert y == round(beta.MOAT_CAP_YEARS["중"] * 0.5, 1)
    y2, _ = beta.competitive_advantage_period(0.20, None, "중")
    assert y2 == round(beta.MOAT_CAP_YEARS["중"] * 0.5, 1)


def test_wacc_uses_measured_tax_and_cost_of_debt():
    # 실측 세율·부채비용 인자를 주면 상수 대신 그 값으로 계산(결측 시 상수 폴백).
    w_const, _ = beta.wacc(0.10, 1000, 500, 0.038)  # 상수 폴백
    w_meas, _ = beta.wacc(0.10, 1000, 500, 0.038, tax_rate=0.08, cost_of_debt=0.035)
    assert w_const != w_meas  # 실측값이 반영돼 달라짐
    # 부채 없으면 세율·부채비용 무관(Re 그대로).
    w_nodebt, _ = beta.wacc(0.10, 1000, 0, 0.038, tax_rate=0.08, cost_of_debt=0.035)
    assert abs(w_nodebt - 0.10) < 1e-9
