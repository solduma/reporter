"""엘리엇 연결형 재귀 파동(상승 추진↔하락 조정 중단없이 연결) 순수 도메인 단위 테스트."""

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


# ── 하드룰 게이트(_impulse_conf) ─────────────────────────────────────────

def test_impulse_conf_accepts_ideal_and_rejects_violation():
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
    assert elliott._impulse_conf(bad, up=True) is None


# ── 연결형 체인 파싱(_parse_chain) ────────────────────────────────────────

def _rising_series() -> list[tuple[str, float]]:
    # 상승 추세 속 여러 상승·하락 스윙(추진↔조정 반복).
    vals = [100, 130, 112, 150, 128, 175, 150, 200, 170, 220, 190, 250]
    return [(f"d{i:02d}", v) for i, v in enumerate(vals)]


def test_parse_chain_alternates_motive_corrective():
    piv = elliott.zigzag(_rising_series(), 0.05)
    chain = elliott._parse_chain(piv, up_trend=True)
    phases = [ph for _, _, ph, _ in chain]
    # motive/corrective 가 교대로 나온다(연속 같은 위상 없음).
    for a, b in pairwise(phases):
        assert a != b


def test_parse_chain_is_gapless():
    piv = elliott.zigzag(_rising_series(), 0.05)
    chain = elliott._parse_chain(piv, up_trend=True)
    # 각 파동 끝 인덱스 = 다음 파동 시작 인덱스(중단 없음).
    for (_, e, _, _), (s2, _, _, _) in pairwise(chain):
        assert e == s2
    # 전 구간 커버(첫 시작=0, 끝=마지막 피벗).
    assert chain[0][0] == 0
    assert chain[-1][1] == len(piv) - 1


# ── analyze 통합: 연결형 + 재귀 + 투영 ────────────────────────────────────

def test_analyze_connected_chain_gapless_and_alternating():
    res = elliott.analyze(_rising_series(), leg_threshold=0.05)
    primary = [s for s in res.segments if s.degree == "primary"]
    assert len(primary) >= 2
    for a, b in pairwise(primary):
        assert a.end_date == b.start_date  # gapless
        assert a.phase != b.phase  # 교대


def test_analyze_has_both_directions():
    # 상승 추세 데이터 — 추진(상승)과 조정(하락)이 모두 등장(하락만 나오는 회귀 방지).
    res = elliott.analyze(_rising_series(), leg_threshold=0.05)
    primary = [s for s in res.segments if s.degree == "primary"]
    dirs = {s.direction for s in primary}
    assert "up" in dirs and "down" in dirs


def test_analyze_projection_is_zone():
    res = elliott.analyze(_rising_series(), leg_threshold=0.05)
    assert res.projection is not None
    assert res.projection.low < res.projection.high
    assert res.projection.basis


def test_analyze_motive_has_sub_labels():
    # 내부에 여러 스윙을 가진 큰 추진 파동은 하위 라벨(1,2,3..)을 가진다(프랙탈). 큰 상승 뒤 큰 조정:
    # 상승 구간이 작은 up-down 스윙 여러 개로 구성되도록 구성.
    vals = [100, 140, 125, 165, 150, 200, 250, 120]  # 큰 상승(내부 스윙 다수) → 큰 하락
    prices = [(f"d{i:02d}", v) for i, v in enumerate(vals)]
    res = elliott.analyze(prices, leg_threshold=0.05)
    motives = [s for s in res.segments if s.degree == "primary" and s.phase == "motive"]
    # 시작 앵커 라벨이 '1'부터 시작하는지(오위치 회귀 방지).
    labeled = [s for s in motives if s.points]
    assert labeled and labeled[0].points[0].label == "1"


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], leg_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"
