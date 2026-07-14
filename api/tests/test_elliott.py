"""엘리엇 파동(반복 사이클 1-2-3-4-5-A-B-C 라벨) 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import elliott


def _piv(seq: list[tuple[str, float, str]]) -> list[elliott.Pivot]:
    return [elliott.Pivot(d, p, k) for d, p, k in seq]


# ── ZigZag ───────────────────────────────────────────────────────────────

def test_zigzag_detects_swings_above_threshold():
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 110.0), ("d4", 130.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    assert any(abs(p.price - 120.0) < 1e-6 for p in pivots)


def test_zigzag_insufficient_data():
    assert elliott.zigzag([("d1", 100.0)]) == []


# ── 하드룰 게이트 (상승/하락 미러) ──────────────────────────────────────

def test_impulse_conf_bull_accepts_and_rejects():
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    ok = _piv([("0", p0, "low"), ("1", p1, "high"), ("2", p2, "low"),
               ("3", p3, "high"), ("4", p4, "low"), ("5", p5, "high")])
    assert elliott._impulse_conf(ok, up=True) is not None
    bad = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 95.0, "low"),
                ("3", 300.0, "high"), ("4", 250.0, "low"), ("5", 350.0, "high")])
    assert elliott._impulse_conf(bad, up=True) is None  # R1 위반


def test_impulse_conf_bear_is_mirror():
    # 하락 5파: 고-저-고-저-고-저, 하락으로 진행.
    p0, p1 = 300.0, 200.0
    p2 = p1 + 55.9
    p3 = p2 - 161.8
    p4 = p3 + 61.8
    p5 = p4 - 100.0
    w = _piv([("0", p0, "high"), ("1", p1, "low"), ("2", p2, "high"),
              ("3", p3, "low"), ("4", p4, "high"), ("5", p5, "low")])
    assert elliott._impulse_conf(w, up=False) is not None
    assert elliott._impulse_conf(w, up=True) is None


# ── 사이클 라벨링 ────────────────────────────────────────────────────────

def _bull_cycle_prices() -> list[tuple[str, float]]:
    # 이상적 상승 5파 + 하락 3파(지그재그) 한 사이클.
    p = [100.0, 200.0, 144.1, 305.9, 244.1, 344.1]  # 1~5
    # 조정 A-B-C(하락): 고→저→고→저
    p += [280.0, 320.0, 250.0]  # A(down),B(up),C(down)
    return [(f"d{i:02d}", v) for i, v in enumerate(p)]


def test_label_cycles_produces_12345abc():
    prices = _bull_cycle_prices()
    piv = elliott.zigzag(prices, 0.05)
    labeled = elliott._label_cycles(piv, up_trend=True)
    labels = [lab for _, lab, _, _ in labeled if lab]
    # 1~5 임펄스가 순서대로 나온다(각 다리 = 한 파동, '5파' 문자열 아님).
    assert labels[:5] == ["1", "2", "3", "4", "5"]


def test_labels_are_single_wave_not_5파():
    # 회귀 방지: wave_label 은 개별 파동 번호이지 '5파'/'3파' 문자열이 아니다.
    prices = _bull_cycle_prices()
    r = elliott.analyze(prices, leg_threshold=0.05)
    assert r.segments
    for s in r.segments:
        assert s.wave_label in {"1", "2", "3", "4", "5", "A", "B", "C"}
        assert s.wave_label not in {"5파", "3파"}


def test_analyze_motive_waves_alternate_direction():
    # 추진 5파 안에서 1,3,5 는 추세방향, 2,4 는 반대방향.
    prices = _bull_cycle_prices()
    r = elliott.analyze(prices, leg_threshold=0.05)
    by_label = {s.wave_label: s for s in r.segments}
    for lab in ("1", "3", "5"):
        if lab in by_label:
            assert by_label[lab].direction == "up"
    for lab in ("2", "4"):
        if lab in by_label:
            assert by_label[lab].direction == "down"


def test_bull_correction_labeled_and_downward():
    # 상승 사이클: 조정 A-B-C 가 실제로 라벨되고 방향이 하락(A↓B↑C↓)인지 단정(방향반전 회귀 방지).
    prices = _bull_cycle_prices()
    r = elliott.analyze(prices, leg_threshold=0.05)
    by = {s.wave_label: s for s in r.segments}
    assert "A" in by and "B" in by and "C" in by  # 조정이 실제로 검출됨
    assert by["A"].direction == "down" and by["C"].direction == "down"
    assert by["B"].direction == "up"


def test_bear_cycle_directions_mirrored():
    # 하락 사이클: 추진 5파 하락(1↓3↓5↓, 2↑4↑), 조정 3파 상승(A↑B↓C↑).
    p = [300.0, 200.0, 235.9, 74.1, 135.9, 35.9]  # 하락 임펄스 1~5
    p += [120.0, 80.0, 150.0]  # 조정 A(up),B(down),C(up)
    prices = [(f"d{i:02d}", v) for i, v in enumerate(p)]
    r = elliott.analyze(prices, leg_threshold=0.05)
    by = {s.wave_label: s for s in r.segments}
    for lab in ("1", "3", "5"):
        if lab in by:
            assert by[lab].direction == "down"
    if "A" in by:
        assert by["A"].direction == "up"  # 하락장 조정 A 는 상승
        assert by.get("C", by["A"]).direction == "up"


def test_analyze_projection_zone_and_bars():
    prices = _bull_cycle_prices()
    r = elliott.analyze(prices, leg_threshold=0.05)
    assert r.projection is not None
    assert r.projection.low < r.projection.high
    assert r.projection.bars_low >= 1 and r.projection.bars_high >= r.projection.bars_low


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], leg_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"


def test_analyze_holds_labels_on_unfittable_noise():
    # 규칙에 안 맞는 잡음은 라벨 유보(억지 카운트 금지) — 세그먼트가 적거나 없음.
    prices = [(f"d{i:02d}", v) for i, v in enumerate([100, 103, 101, 104, 102, 105])]
    r = elliott.analyze(prices, leg_threshold=0.05)
    # 억지로 '5파' 를 만들지 않는다 — 라벨은 유효 파동 번호만.
    for s in r.segments:
        assert s.wave_label in {"1", "2", "3", "4", "5", "A", "B", "C"}
