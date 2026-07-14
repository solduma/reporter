"""엘리엇 파동(연속 위상 교대 + 프랙탈 + 투영) 순수 도메인 단위 테스트."""

from __future__ import annotations

from itertools import pairwise

from app.domain import elliott


def _piv(seq: list[tuple[str, float, str]]) -> list[elliott.Pivot]:
    return [elliott.Pivot(d, p, k) for d, p, k in seq]


# ── ZigZag ───────────────────────────────────────────────────────────────

def test_zigzag_detects_swings_above_threshold():
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 110.0), ("d4", 130.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    assert any(abs(p.price - 120.0) < 1e-6 for p in pivots)


def test_zigzag_ignores_small_reversals():
    prices = [("d1", 100.0), ("d2", 105.0), ("d3", 103.0), ("d4", 108.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    assert all(abs(p.price - 103.0) > 1e-6 for p in pivots)


def test_zigzag_insufficient_data():
    assert elliott.zigzag([("d1", 100.0)]) == []


# ── 재귀 ZigZag nesting ──────────────────────────────────────────────────

def test_recursive_zigzag_is_subset():
    prices = [(f"d{i:02d}", v) for i, v in enumerate(
        [100, 130, 112, 160, 140, 200, 175, 230, 150, 240, 130, 260]
    )]
    minor = elliott.zigzag(prices, 0.05)
    major = elliott.recursive_zigzag(minor, 0.15)
    minor_dates = {p.date for p in minor}
    assert all(p.date in minor_dates for p in major)
    assert len(major) <= len(minor)


# ── 하드룰 게이트 ─────────────────────────────────────────────────────────

def test_impulse_conf_accepts_ideal_and_rejects_violations():
    p0, p1 = 100.0, 200.0
    p2, p3, p4, p5 = p1 - 55.9, (p1 - 55.9) + 161.8, (p1 - 55.9) + 161.8 - 61.8, 0.0
    p5 = p4 + 100.0
    ok = _piv([("0", p0, "low"), ("1", p1, "high"), ("2", p2, "low"),
               ("3", p3, "high"), ("4", p4, "low"), ("5", p5, "high")])
    assert elliott._impulse_conf(ok, up=True) is not None
    # 2파 완전 되돌림(R1 위반).
    bad = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 95.0, "low"),
                ("3", 300.0, "high"), ("4", 250.0, "low"), ("5", 350.0, "high")])
    assert elliott._impulse_conf(bad, up=True) is None


def test_impulse_conf_bear_mirror():
    p0, p1 = 300.0, 200.0
    p2 = p1 + 55.9
    p3 = p2 - 161.8
    p4 = p3 + 61.8
    p5 = p4 - 100.0
    w = _piv([("0", p0, "high"), ("1", p1, "low"), ("2", p2, "high"),
              ("3", p3, "low"), ("4", p4, "high"), ("5", p5, "low")])
    assert elliott._impulse_conf(w, up=False) is not None
    assert elliott._impulse_conf(w, up=True) is None


# ── analyze 통합: 연속 위상 교대 + 강조 + 투영 ────────────────────────────

def _impulse_prices() -> list[tuple[str, float]]:
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    return [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]


def test_analyze_emphasizes_impulse_with_own_points():
    res = elliott.analyze(_impulse_prices(), leg_threshold=0.05)
    assert res.labeled is True
    assert res.direction == "up"
    imp = [s for s in res.segments if s.layer == "impulse"]
    assert len(imp) == 1
    assert [pt.label for pt in imp[0].points] == ["0", "1", "2", "3", "4", "5"]


def test_analyze_leg_chain_is_gapless():
    # 여러 스윙 데이터 — leg 세그먼트가 끝=다음시작으로 중단없이 이어진다(gapless).
    prices = [(f"d{i:02d}", v) for i, v in enumerate(
        [100, 130, 110, 150, 125, 175, 140, 200, 160, 210, 150, 230]
    )]
    res = elliott.analyze(prices, leg_threshold=0.05)
    legs = [s for s in res.segments if s.layer == "leg"]
    assert len(legs) >= 2
    for a, b in pairwise(legs):
        assert a.end_date == b.start_date  # gap 없음


def test_analyze_leg_phases_alternate():
    # 위상은 motive/corrective 로 교대(연속 라벨). 둘 다 등장.
    prices = [(f"d{i:02d}", v) for i, v in enumerate(
        [100, 130, 110, 150, 125, 175, 140, 200, 160, 210, 150, 230]
    )]
    res = elliott.analyze(prices, leg_threshold=0.05)
    phases = [s.phase for s in res.segments if s.layer == "leg"]
    assert "motive" in phases and "corrective" in phases


def test_analyze_projection_is_zone():
    res = elliott.analyze(_impulse_prices(), leg_threshold=0.05)
    assert res.projection is not None
    assert res.projection.low < res.projection.high  # 단일 선이 아닌 구간
    assert res.projection.basis


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], leg_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"


def test_analyze_no_impulse_still_has_gapless_legs():
    # 임펄스 규칙 미달 잡음이어도 전 구간 leg 위상 교대는 gapless 로 채워진다.
    prices = [(f"d{i:02d}", v) for i, v in enumerate([100, 108, 101, 109, 102, 110])]
    res = elliott.analyze(prices, leg_threshold=0.05)
    legs = [s for s in res.segments if s.layer == "leg"]
    if len(legs) >= 2:
        for a, b in pairwise(legs):
            assert a.end_date == b.start_date
