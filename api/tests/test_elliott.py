"""엘리엇 파동 추정(ZigZag + 5파 라벨) 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import elliott


def _line(points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    return points


def test_zigzag_detects_swings_above_threshold():
    # 100→120(+20%)→110(-8.3%)→130 : 두 반전이 임계(8%) 넘음.
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 110.0), ("d4", 130.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    kinds = [p.kind for p in pivots]
    # 첫 고점(120)·저점(110) 확정 + 마지막 잠정(130).
    assert "high" in kinds and "low" in kinds
    assert any(abs(p.price - 120.0) < 1e-6 for p in pivots)
    assert any(abs(p.price - 110.0) < 1e-6 for p in pivots)


def test_zigzag_ignores_small_reversals():
    # 100→105→103→108 : 되돌림(-1.9%)이 임계(8%) 미만 → 반전 무시(극점 연장).
    # 앵커 100 에서 +8% 상승(108)해 저점 확정 + 마지막 고점(108, 잠정). 중간 103 은 무시.
    prices = [("d1", 100.0), ("d2", 105.0), ("d3", 103.0), ("d4", 108.0)]
    pivots = elliott.zigzag(prices, threshold=0.08)
    # 중간 되돌림이 피벗을 만들지 않는다(103 이 피벗에 없음).
    assert all(abs(p.price - 103.0) > 1e-6 for p in pivots)
    assert abs(pivots[-1].price - 108.0) < 1e-6
    assert pivots[-1].kind == "high"


def test_zigzag_insufficient_data():
    assert elliott.zigzag([("d1", 100.0)]) == []
    assert elliott.zigzag([]) == []


def _impulse_prices() -> list[tuple[str, float]]:
    # 피보 비율에 근접한 이상적 5파 상승: 1파+100, 2파 되돌림 ~0.559, 3파 ~1.618, 4파 ~0.382.
    # 저-고-저-고-저-고 피벗을 만들도록 큰 스윙으로 구성(임계 8% 초과).
    p0, p1 = 100.0, 200.0  # w1 = 100
    p2 = p1 - 55.9  # w2 = 55.9 (0.559*w1)
    p3 = p2 + 161.8  # w3 = 161.8 (1.618*w1)
    p4 = p3 - 61.8  # w4 = 61.8 (0.382*w3)
    p5 = p4 + 100.0  # w5
    pts = [("d0", p0), ("d1", p1), ("d2", p2), ("d3", p3), ("d4", p4), ("d5", p5)]
    return pts


def test_label_impulse_valid_five_wave():
    pivots = elliott.zigzag(_impulse_prices(), threshold=0.08)
    labeled, conf, direction = elliott.label_impulse(pivots)
    assert labeled is True
    assert direction == "up"
    assert conf >= elliott.MIN_LABEL_CONFIDENCE
    # 라벨 0~5 부여.
    labels = [p.label for p in pivots[-6:]]
    assert labels == ["0", "1", "2", "3", "4", "5"]


def test_label_impulse_rule2_violation_when_wave2_retraces_fully():
    # 2파가 1파를 100% 되돌리면(저점이 1파 시작 이하) 규칙1 위반 → 라벨 안 됨.
    pivots = [
        elliott.Pivot("d0", 100.0, "low"),
        elliott.Pivot("d1", 200.0, "high"),
        elliott.Pivot("d2", 95.0, "low"),  # w2 > w1
        elliott.Pivot("d3", 300.0, "high"),
        elliott.Pivot("d4", 250.0, "low"),
        elliott.Pivot("d5", 350.0, "high"),
    ]
    labeled, _, _ = elliott.label_impulse(pivots)
    assert labeled is False


def test_label_impulse_rule_overlap_violation():
    # 4파 저점이 1파 고점 아래로 침범하면 규칙3 위반.
    pivots = [
        elliott.Pivot("d0", 100.0, "low"),
        elliott.Pivot("d1", 200.0, "high"),
        elliott.Pivot("d2", 150.0, "low"),
        elliott.Pivot("d3", 300.0, "high"),
        elliott.Pivot("d4", 190.0, "low"),  # < p1(200) → 중첩
        elliott.Pivot("d5", 350.0, "high"),
    ]
    labeled, _, _ = elliott.label_impulse(pivots)
    assert labeled is False


def test_label_impulse_detects_bear_five_wave():
    # 하락 5파(고-저-고-저-고-저) — 상승 임펄스를 부호만 뒤집은 이상적 피보 비율.
    p0, p1 = 300.0, 200.0  # w1 = 100 하락
    p2 = p1 + 55.9  # 2파 반등 0.559
    p3 = p2 - 161.8  # 3파 하락 1.618
    p4 = p3 + 61.8  # 4파 반등 0.382
    p5 = p4 - 100.0  # 5파 하락
    pivots = [
        elliott.Pivot("d0", p0, "high"),
        elliott.Pivot("d1", p1, "low"),
        elliott.Pivot("d2", p2, "high"),
        elliott.Pivot("d3", p3, "low"),
        elliott.Pivot("d4", p4, "high"),
        elliott.Pivot("d5", p5, "low"),
    ]
    labeled, conf, direction = elliott.label_impulse(pivots)
    assert labeled is True
    assert direction == "down"
    assert conf >= elliott.MIN_LABEL_CONFIDENCE


def test_label_impulse_scans_window_not_only_last_six():
    # 유효 5파 뒤에 미확정 스윙 피벗이 더 붙어도(마지막 6개가 아님) 슬라이딩으로 찾아낸다.
    imp = elliott.zigzag(_impulse_prices(), threshold=0.08)  # 6피벗 상승 임펄스
    trailing = [elliott.Pivot("d6", imp[-1].price * 0.9, "low")]  # 진행 중 조정 스윙
    labeled, _, direction = elliott.label_impulse(imp + trailing)
    assert labeled is True  # 마지막 6개만 봤다면 못 찾았을 케이스
    assert direction == "up"


def test_analyze_returns_pivots_even_without_label():
    # 라벨이 안 붙어도 피벗과 note 는 준다.
    prices = [("d1", 100.0), ("d2", 120.0), ("d3", 108.0), ("d4", 125.0)]
    res = elliott.analyze(prices, threshold=0.08)
    assert res.pivots
    assert res.labeled is False
    assert res.direction == "none"
    assert "스윙" in res.note or "부족" in res.note
