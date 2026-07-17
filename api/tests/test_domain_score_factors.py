"""테크노펀더멘탈 축 점수 '계산 근거' 분해 테스트 — 요소 라벨·정규화·가중치 검증."""

from __future__ import annotations

from app.domain import score_factors as sf


def _by_label(factors, label):
    return next(f for f in factors if f.label == label)


def test_growth_factors_seven_elements():
    # 매출 YoY + 각 이익의 상태·마진율 증감 분리(7요소). 상태는 배지, 마진은 pp 표기.
    fs = sf.growth_factors(
        revenue_yoy=0.6, op_status="흑자지속", op_margin_delta=0.05,
        net_status="흑자전환", net_margin_delta=0.04,
        ebitda_status="적자지속", ebitda_margin_delta=0.06,
    )
    assert _by_label(fs, "매출 YoY").norm == 1.0
    op = _by_label(fs, "영업이익")
    opm = _by_label(fs, "영업이익률(OPM) 증감")
    assert op.value == "흑자지속" and op.norm == 0.7 and op.weight == 0.16
    assert opm.value == "+5.0pp" and opm.weight == 0.14
    assert _by_label(fs, "순이익").value == "흑자전환" and _by_label(fs, "순이익").norm == 1.0
    assert _by_label(fs, "순이익률(NPM) 증감").value == "+4.0pp"
    assert _by_label(fs, "EBITDA").value == "적자지속" and _by_label(fs, "EBITDA").norm == 0.0
    assert _by_label(fs, "EBITDA마진 증감").value == "+6.0pp"


def test_growth_factors_status_and_margin_separated():
    # 상태 행과 마진 행이 분리 노출되고 각각 점수·근거가 일치한다(마진 이중계산 없음).
    fs = sf.growth_factors(0.3, "흑자전환", 0.30)
    labels = [f.label for f in fs]
    assert "영업이익 YoY" not in labels
    assert _by_label(fs, "영업이익").norm == sf.status_norm("흑자전환")  # 상태 축
    assert _by_label(fs, "영업이익률(OPM) 증감").norm == sf.margin_pp_score(0.30)  # 마진 축
    # 마진 개선폭이 크면 OPM 행 norm 이 높다.
    big = _by_label(sf.growth_factors(0.3, "흑자전환", 0.30), "영업이익률(OPM) 증감").norm
    small = _by_label(sf.growth_factors(0.3, "흑자전환", 0.005), "영업이익률(OPM) 증감").norm
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


def test_value_factors_peg_surrogate_shows_status():
    # eps_yoy 로 PEG 수치를 못 구해도(흑자전환) 대체점이 있으면 근거 표시값이 상태 라벨이고
    # norm(점수 기여)도 함께 채워져 점수-근거가 어긋나지 않는다.
    fs = sf.value_factors(
        per=15, pbr=1.0, ev_ebitda=8, roe=12, div_yield=2,
        per_rank=0.6, pbr_rank=0.8, ev_rank=0.7,
        peg_rank=0.7, peg_value=None, peg_surrogate_status="흑자전환",
    )
    peg = _by_label(fs, "PEG")
    assert peg.value == "흑자전환" and peg.norm == 0.7


def test_value_factors_peg_numeric_takes_precedence():
    # 실측 PEG 값이 있으면 상태 라벨이 아니라 수치를 표시.
    fs = sf.value_factors(
        per=15, pbr=1.0, ev_ebitda=8, roe=12, div_yield=2,
        per_rank=0.6, pbr_rank=0.8, ev_rank=0.7,
        peg_rank=1.0, peg_value=0.15, peg_surrogate_status="흑자전환",
    )
    assert _by_label(fs, "PEG").value == "0.15"


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
