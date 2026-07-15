"""테크노펀더멘탈 축 점수 '계산 근거' 분해 테스트 — 요소 라벨·정규화·가중치 검증."""

from __future__ import annotations

from app.domain import score_factors as sf


def _by_label(factors, label):
    return next(f for f in factors if f.label == label)


def test_growth_factors_three_elements():
    # 매출 YoY + 영업이익(상태+pp 결합) + EPS YoY. 가중치 매출0.4·영업0.35·EPS0.25.
    fs = sf.growth_factors(revenue_yoy=0.6, op_status="흑자지속", op_margin_delta=0.05, eps_yoy=0.3)
    rev = _by_label(fs, "매출 YoY")
    op = _by_label(fs, "영업이익")
    eps = _by_label(fs, "EPS YoY")
    assert rev.norm == 1.0 and rev.weight == 0.4 and rev.value == "+60%"
    assert op.weight == 0.35 and "흑자지속" in op.value and "+5.0pp" in op.value
    assert eps.weight == 0.25 and eps.value == "+30%"


def test_growth_factors_turnaround_shows_status_and_pp():
    # 흑전은 영업이익 행 하나에 상태 + 규모(pp)를 함께 표기. 별도 정의불가 행 없음.
    fs = sf.growth_factors(0.3, "흑자전환", 0.30, None)
    labels = [f.label for f in fs]
    assert "영업이익 YoY" not in labels  # 정의불가 YoY 행 없음
    op = _by_label(fs, "영업이익")
    assert op.value == "흑자전환 +30.0pp"
    assert op.norm == sf.op_profit_norm("흑자전환", 0.30)  # 점수·근거 일치
    # 규모 큰 흑전이 작은 흑전보다 norm 높음.
    big = _by_label(sf.growth_factors(0.3, "흑자전환", 0.30), "영업이익").norm
    small = _by_label(sf.growth_factors(0.3, "흑자전환", 0.005), "영업이익").norm
    assert big > small


def test_value_factors_missing_is_none():
    fs = sf.value_factors(
        per=None, pbr=0.5, ev_ebitda=None, roe=None, div_yield=None,
        per_rank=None, pbr_rank=1.0, ev_rank=None,
    )
    assert _by_label(fs, "저PBR").norm == 1.0
    assert _by_label(fs, "저PER").norm is None  # 결측 → 기여 0
    assert _by_label(fs, "PEG").norm is None  # PEG 미제공 → 기여 0
    assert _by_label(fs, "ROE 가점").value == "—"


def test_trend_factors_alignment_label():
    fs = sf.trend_factors(
        near_high_pct=100.0, ma_aligned=True, above_ma120=True, vol_ratio=2.0, return_3m=40.0,
    )
    assert _by_label(fs, "52주 신고가 근접").norm == 1.0
    align = _by_label(fs, "이평 정배열")
    assert align.norm == 1.0 and align.value == "정배열"
    # 정배열 아님이지만 MA120 위 → norm 1.0, 라벨 구분.
    fs2 = sf.trend_factors(near_high_pct=None, ma_aligned=False, above_ma120=True,
                           vol_ratio=None, return_3m=None)
    a2 = _by_label(fs2, "이평 정배열")
    assert a2.norm == 1.0 and a2.value == "MA120 위"
    # 역배열 → 0.
    fs3 = sf.trend_factors(near_high_pct=None, ma_aligned=False, above_ma120=False,
                           vol_ratio=None, return_3m=None)
    assert _by_label(fs3, "이평 정배열").norm == 0.0


def test_topdown_factors_flow_normalization():
    fs = sf.topdown_factors(us_flow=90.0, kr_flow=None, kr_index_flow=60.0)
    assert _by_label(fs, "미국 섹터 수급(선행)").norm == 0.9
    assert _by_label(fs, "국내 섹터 수급").norm is None
    idx = _by_label(fs, "국내 지수 수급")
    assert idx.norm == 0.6 and idx.value == "60.0점"


def test_factors_payload_shape():
    payload = sf.factors_payload(sf.GROWTH_METHOD, sf.growth_factors(0.3, 0.3, False))
    assert payload.get("method")
    assert all({"label", "value", "norm", "weight"} <= set(f) for f in payload["factors"])
