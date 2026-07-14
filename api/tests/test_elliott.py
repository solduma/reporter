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

def test_correction_conf_accepts_zigzag_down():
    # 상승 추세 속 하락 조정(고-저-고-저): A 하락, B 되돌림, C 하락.
    w = _piv([("0", 200.0, "high"), ("A", 150.0, "low"),
              ("B", 175.0, "high"), ("C", 145.0, "low")])
    assert elliott._correction_conf(w, up=True) is not None


def test_correction_conf_rejects_wrong_shape():
    # 상승 추세인데 조정 피벗이 저-고-저-고(상승형) → None.
    w = _piv([("0", 150.0, "low"), ("A", 200.0, "high"),
              ("B", 170.0, "low"), ("C", 210.0, "high")])
    assert elliott._correction_conf(w, up=True) is None


# ── 재귀 ZigZag 프랙탈 nesting ───────────────────────────────────────────

def test_recursive_zigzag_is_subset_of_minor():
    # major 피벗은 반드시 minor 피벗의 부분집합(엄격한 nesting).
    prices = [(f"d{i:02d}", v) for i, v in enumerate(
        [100, 130, 112, 160, 140, 200, 175, 230, 150, 240, 130, 260]
    )]
    minor = elliott.zigzag(prices, 0.05)
    major = elliott.recursive_zigzag(minor, 0.13)
    minor_dates = {p.date for p in minor}
    assert all(p.date in minor_dates for p in major)
    assert len(major) <= len(minor)


# ── analyze 통합 ─────────────────────────────────────────────────────────

def test_analyze_labels_full_five_wave_impulse():
    # 이상적 상승 5파 → 최소 1개 impulse 세그먼트 + 방향 up + 현재 위치 문구.
    p0, p1 = 100.0, 200.0
    p2 = p1 - 55.9
    p3 = p2 + 161.8
    p4 = p3 - 61.8
    p5 = p4 + 100.0
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    res = elliott.analyze(prices, minor_threshold=0.05)
    assert res.labeled is True
    assert res.direction == "up"
    assert any(s.kind == "impulse" for s in res.segments)
    assert res.current_position  # 비어있지 않음
    # 세부 세그먼트 라벨이 피벗에 부여됨.
    assert any(p.label for p in res.pivots)


def test_analyze_returns_pivots_even_without_label():
    # 파동 구조가 없으면 피벗만, labeled=False, 방향 none.
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 108.0), ("d4", 125.0)]
    res = elliott.analyze(prices, minor_threshold=0.05)
    assert res.labeled is False
    assert res.direction == "none"
    assert res.segments == []


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], minor_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"


def test_analyze_current_position_holds_on_complex_region():
    # 이상적 5파 뒤에 방향 안 맞는 잡음 스윙을 여럿 붙이면 '복합 구간 라벨 유보'로 정직 처리.
    p0, p1 = 100.0, 200.0
    p2, p3, p4, p5 = 144.1, 305.9, 244.1, 344.1
    prices = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    # 5파 뒤 큰 스윙 4개(라벨 안 맞는 진행 레그) 추가.
    tail = [("d6", 300.0), ("d7", 360.0), ("d8", 300.0), ("d9", 380.0), ("d10", 310.0)]
    res = elliott.analyze(prices + tail, minor_threshold=0.05)
    # 현재 위치는 항상 사람이 읽는 문구(추진/조정/유보 등)를 낸다 — 빈 문자열이 아니다.
    assert res.labeled is True
    assert res.current_position and res.current_position != "구조 불명 — 스윙만 표시"
