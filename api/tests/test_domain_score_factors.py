"""테크노펀더멘탈 축 점수 '계산 근거' 분해 테스트 — 요소 라벨·정규화·가중치 검증."""

from __future__ import annotations

from app.domain import score_factors as sf


def _by_label(factors, label):
    return next(f for f in factors if f.label == label)


def test_growth_factors_four_elements():
    # 비흑전: 매출·영업이익·EPS YoY + OPM 개선 4요소. 가중치 매출0.35·영업0.30·EPS0.20·OPM0.15.
    fs = sf.growth_factors(revenue_yoy=0.6, op_yoy=-0.2, op_turnaround=False, op_margin_delta=0.05, eps_yoy=0.3)
    rev = _by_label(fs, "매출 YoY")
    op = _by_label(fs, "영업이익 YoY")
    eps = _by_label(fs, "EPS YoY")
    opm = _by_label(fs, "영업이익률 개선")
    assert rev.norm == 1.0 and rev.weight == 0.35 and rev.value == "+60%"
    assert op.norm == 0.0 and op.weight == 0.30  # -20% → 하단
    assert eps.weight == 0.20 and eps.value == "+30%"
    assert opm.weight == 0.15 and opm.value == "+5.0pp"


def test_growth_factors_turnaround_merges_op_into_recovery():
    # 흑전은 정의 불가한 '영업이익 YoY'·'EPS YoY' 행을 숨기고, 영업이익 회복을 '영업이익 회복(흑전)'
    # 한 행(가중치 OPM 0.15)으로 노출 — pp 로 규모 표기. 매출 행은 그대로.
    fs_big = sf.growth_factors(0.3, None, True, 0.30, None)
    labels = [f.label for f in fs_big]
    assert "영업이익 YoY" not in labels and "EPS YoY" not in labels
    rec = _by_label(fs_big, "영업이익 회복(흑전)")
    assert rec.norm == 1.0 and rec.value == "+30.0pp" and rec.weight == 0.15
    small = _by_label(sf.growth_factors(0.3, None, True, 0.0, None), "영업이익 회복(흑전)")
    assert small.norm == 0.5 and small.value == "+0.0pp"  # 보합


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
