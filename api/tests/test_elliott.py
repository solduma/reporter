"""엘리엇 파동 재설계 순수 도메인 단위 테스트 — 하드룰 게이트·ABC·체인·프랙탈·현재 위치."""

from __future__ import annotations

from app.domain import elliott


def test_zigzag_detects_swings_above_threshold():
    # 100→120(+20%)→110(-8.3%)→130 : 두 반전이 임계(8%) 넘음.
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 110.0), ("d4", 130.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    kinds = [p.kind for p in pivots]
    assert "high" in kinds and "low" in kinds
    assert any(abs(p.price - 120.0) < 1e-6 for p in pivots)


def test_zigzag_ignores_small_reversals():
    # 100→105→103→108 : 되돌림(-1.9%)이 임계(8%) 미만 → 반전 무시.
    prices = [("d1", 100.0), ("d2", 105.0), ("d3", 103.0), ("d4", 108.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    assert all(abs(p.price - 103.0) > 1e-6 for p in pivots)


def test_zigzag_insufficient_data():
    assert elliott.zigzag([("d1", 100.0)]) == []
    assert elliott.zigzag([]) == []


# ── 하드룰 게이트(_impulse_conf) ─────────────────────────────────────────

def _piv(seq: list[tuple[str, float, str]]) -> list[elliott.Pivot]:
    return [elliott.Pivot(d, p, k) for d, p, k in seq]


def test_impulse_conf_accepts_ideal_five_wave():
    # 이상적 피보 비율 상승 5파(저-고-저-고-저-고).
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9  # w2 ≈ 0.559*w1
    p3 = p2 + 161.8  # w3 ≈ 1.618*w1
    p4 = p3 - 61.8  # w4 ≈ 0.382*w3
    p5 = p4 + 100.0
    w = _piv([("0", p0, "low"), ("1", p1, "high"), ("2", p2, "low"),
              ("3", p3, "high"), ("4", p4, "low"), ("5", p5, "high")])
    conf = elliott._impulse_conf(w, up=True)
    assert conf is not None and conf >= 0.5


def test_impulse_conf_rejects_wave2_full_retrace():
    # 2파가 1파 시작 아래로(비침범 위반, R1).
    w = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 95.0, "low"),
              ("3", 300.0, "high"), ("4", 250.0, "low"), ("5", 350.0, "high")])
    assert elliott._impulse_conf(w, up=True) is None


def test_impulse_conf_rejects_wave4_overlap():
    # 4파 저점이 1파 고점 아래로 침범(R3 위반).
    w = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 150.0, "low"),
              ("3", 300.0, "high"), ("4", 190.0, "low"), ("5", 350.0, "high")])
    assert elliott._impulse_conf(w, up=True) is None


def test_impulse_conf_bear_mirror():
    # 하락 5파(고-저-고-저-고-저) — 상승을 부호만 뒤집은 이상적 비율.
    p0, p1 = 300.0, 200.0
    p2 = p1 + 55.9
    p3 = p2 - 161.8
    p4 = p3 + 61.8
    p5 = p4 - 100.0
    w = _piv([("0", p0, "high"), ("1", p1, "low"), ("2", p2, "high"),
              ("3", p3, "low"), ("4", p4, "high"), ("5", p5, "low")])
    conf = elliott._impulse_conf(w, up=False)
    assert conf is not None and conf >= 0.5
    # 방향이 안 맞으면(상승으로 검사) None.
    assert elliott._impulse_conf(w, up=True) is None


# ── ABC 조정(_correction_conf) ───────────────────────────────────────────

# ── 기본 다리 레이어(_leg_segments) ─────────────────────────────────────

def test_leg_segments_alternate_up_down():
    # 인접 피벗 다리는 상승/하락이 교대로 나온다(전 구간 흐름 균형).
    pivots = _piv([("d0", 100.0, "low"), ("d1", 130.0, "high"),
                   ("d2", 110.0, "low"), ("d3", 150.0, "high")])
    legs = elliott._leg_segments(pivots)
    assert [s.direction for s in legs] == ["up", "down", "up"]
    assert all(s.layer == "leg" for s in legs)


# ── analyze 통합(하이브리드) ─────────────────────────────────────────────

def test_analyze_emphasizes_five_wave_impulse():
    # 이상적 상승 5파 → 강조 impulse 세그먼트 1개(방향 up) + 라벨 부여.
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    res = elliott.analyze(prices, leg_threshold=0.05)
    assert res.labeled is True
    assert res.direction == "up"
    imp = [s for s in res.segments if s.layer == "impulse"]
    assert len(imp) == 1 and imp[0].direction == "up"
    # 임펄스는 자체 라벨 6점(0~5)을 보유한다(base pivots 가 아니라 세그먼트가 소유).
    assert [pt.label for pt in imp[0].points] == ["0", "1", "2", "3", "4", "5"]


def test_analyze_finds_impulse_only_visible_at_coarse_threshold():
    # 5파 각 파동 사이에 작은 되돌림 잡음을 끼워, 가는 임계(leg 6%)로는 스윙이 쪼개져 임펄스 규칙을
    # 못 맞추지만 굵은 임계(8·10%)로는 잡히는 상승 5파 — 다중 임계 스캔이 복원해야 한다.
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    res = elliott.analyze(prices, leg_threshold=0.06)
    imp = [s for s in res.segments if s.layer == "impulse"]
    assert len(imp) == 1 and imp[0].direction == "up"  # 굵은 임계에서 상승 5파 검출


def test_analyze_deduplicates_overlapping_impulses():
    # 같은 상승 5파가 여러 임계에서 잡혀도 날짜구간 겹침 제거로 하나만 남는다.
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    res = elliott.analyze(prices, leg_threshold=0.05)
    imp = [s for s in res.segments if s.layer == "impulse"]
    assert len(imp) == 1  # 중복 채택 안 됨


def test_analyze_always_has_leg_segments_both_directions():
    # 상승 추세 잡음 데이터 — 강조 5파가 없어도 기본 다리는 상승·하락 모두 나온다(하락 도배 방지).
    prices = [(f"d{i:02d}", v) for i, v in enumerate(
        [100, 112, 104, 118, 109, 124, 115, 130]
    )]
    res = elliott.analyze(prices, leg_threshold=0.05)
    legs = [s for s in res.segments if s.layer == "leg"]
    assert any(s.direction == "up" for s in legs)
    assert any(s.direction == "down" for s in legs)


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], leg_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"


def test_analyze_current_position_holds_on_complex_region():
    # 이상적 5파 뒤에 방향 안 맞는 큰 스윙을 여럿 붙이면 '복합 구간 라벨 유보'.
    p0, p1 = 100.0, 200.0
    p2, p3, p4, p5 = 144.1, 305.9, 244.1, 344.1
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    tail = [("d6", 260.0), ("d7", 330.0), ("d8", 270.0), ("d9", 350.0), ("d10", 280.0)]
    res = elliott.analyze(prices + tail, leg_threshold=0.05)
    assert res.labeled is True
    assert "유보" in res.current_position
