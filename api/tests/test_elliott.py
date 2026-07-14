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


def test_impulse_conf_rejects_r2_only():
    # R2 단독 위반: 3파(45)가 1·3·5 중 최단. R1(2파<1파)·R3(4파가 1파 끝 비중첩)는 만족.
    # R2 검사를 지우면 통과하므로(비등가 뮤턴트) 규칙이 실제로 작동하는지 가드한다.
    w = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 150.0, "low"),
              ("3", 245.0, "high"), ("4", 240.0, "low"), ("5", 500.0, "high")])
    assert elliott._impulse_conf(w, up=True) is None  # R2 위반으로 거부


def test_impulse_conf_rejects_r3_only():
    # R3 단독 위반: 4파 저점(180)이 1파 고점(200)을 침범(중첩). R1·R2는 만족.
    w = _piv([("0", 100.0, "low"), ("1", 200.0, "high"), ("2", 170.0, "low"),
              ("3", 370.0, "high"), ("4", 180.0, "low"), ("5", 430.0, "high")])
    assert elliott._impulse_conf(w, up=True) is None  # R3 위반으로 거부


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
    labeled = elliott._label_cycles(piv)
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


def _odd_motive_dirs(r: elliott.ElliottResult) -> set[str]:
    # 홀수 파동(1·3·5)만 추진의 실제 방향을 담는다. 2·4파는 어느 상승 임펄스든 항상 down 이라
    # 전체 motive 방향집합으로 판정하면 하락 임펄스가 0개여도 통과하는 헛된 단정이 된다.
    return {s.direction for s in r.segments if s.phase == "motive" and s.wave_label in ("1", "3", "5")}


def test_down_trend_labels_down_motive():
    # 사용자 버그 회귀 가드: 하락 추세의 추진은 하락 5파여야 한다(전역/방향 고정으로 상승 라벨 금지).
    dn = [300.0, 200.0, 235.9, 74.1, 135.9, 35.9]  # 하락 5파
    cor = [120.0, 80.0, 150.0]  # 조정 A↑B↓C↑(하락장 반전)
    dn2 = [100.0, 66.6, 78.4, 24.7, 45.2, 12.0]  # 재하락 5파
    prices = [(f"d{i:03d}", v) for i, v in enumerate(dn + cor + dn2)]
    dirs = _odd_motive_dirs(elliott.analyze(prices, leg_threshold=0.05))
    assert dirs == {"down"}  # 하락 추세엔 하락 추진만


def test_up_trend_labels_up_motive():
    # 대칭 가드: 상승 추세의 추진은 상승 5파여야 한다(상승 파동에 조정 3파 오라벨 금지).
    up = [100.0, 200.0, 144.1, 305.9, 244.1, 344.1]  # 상승 5파
    cor = [280.0, 320.0, 250.0]  # 조정 A↓B↑C↓
    up2 = [350.0, 450.0, 394.1, 555.9, 494.1, 594.1]  # 재상승 5파
    prices = [(f"d{i:03d}", v) for i, v in enumerate(up + cor + up2)]
    dirs = _odd_motive_dirs(elliott.analyze(prices, leg_threshold=0.05))
    assert dirs == {"up"}  # 상승 추세엔 상승 추진만


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


def test_projection_direction_opposes_last_motive():
    # 투영 부호 가드: 상승 추진 완성 뒤 다음 조정 zone 은 마지막가 아래, 하락 추진 뒤엔 위.
    # (조정 방향·투영 부호를 결정하는 last_motive_up 부기가 뒤집히면 이 단정이 깨진다.)
    up = [(f"d{i:02d}", v) for i, v in enumerate([100.0, 200.0, 144.1, 305.9, 244.1, 344.1])]
    ru = elliott.analyze(up, leg_threshold=0.05)
    assert ru.segments[-1].direction == "up"
    assert ru.projection is not None and ru.projection.high < ru.segments[-1].end_price

    dn = [(f"d{i:02d}", v) for i, v in enumerate([300.0, 200.0, 235.9, 74.1, 135.9, 35.9])]
    rd = elliott.analyze(dn, leg_threshold=0.05)
    assert rd.segments[-1].direction == "down"
    assert rd.projection is not None and rd.projection.low > rd.segments[-1].end_price


def test_analyze_insufficient_pivots():
    res = elliott.analyze([("d1", 100.0)], leg_threshold=0.05)
    assert res.labeled is False
    assert res.current_position == "피벗 부족"


def test_labels_are_only_valid_wave_symbols():
    # 라벨은 유효 파동 번호(1~5·A~C)만 — '5파' 같은 문자열이나 잡값 금지.
    prices = [(f"d{i:02d}", v) for i, v in enumerate([100, 103, 101, 104, 102, 105])]
    r = elliott.analyze(prices, leg_threshold=0.05)
    for s in r.segments:
        assert s.wave_label in {"1", "2", "3", "4", "5", "A", "B", "C"}


def _many_pivots() -> list[tuple[str, float]]:
    # 여러 사이클이 나오도록 상승·하락·조정을 반복한 긴 시계열.
    seg1 = [100.0, 200.0, 144.1, 305.9, 244.1, 344.1]  # 상승 5파
    cor1 = [280.0, 320.0, 250.0]  # 조정 A-B-C
    seg2 = [400.0, 300.0, 335.9, 174.1, 235.9, 135.9]  # 하락 5파
    cor2 = [200.0, 160.0, 240.0]  # 조정 A-B-C
    seg3 = [180.0, 280.0, 224.1, 385.9, 324.1, 424.1]  # 상승 5파
    vals = seg1 + cor1 + seg2 + cor2 + seg3
    return [(f"d{i:03d}", v) for i, v in enumerate(vals)]


def test_chain_is_gapless():
    # 연속 체인의 핵심 불변식: 라벨된 다리가 피벗 인덱스로 끊김 없이 이어진다(갭 0).
    # (정렬 보정으로 사이클 사이에 연결용 저신뢰 다리가 낄 수 있어 위상이 정확히 5-3-5-3은 아님.)
    piv = elliott.zigzag(_many_pivots(), 0.05)
    labeled = [(si, lab) for si, lab, _, _ in elliott._label_cycles(piv) if lab]
    idxs = [si for si, _ in labeled]
    assert idxs == list(range(len(piv) - 1))  # 갭 없이 전 다리 라벨(마지막 피벗 제외)


def test_high_confidence_motive_and_correction_both_present():
    # 정렬 유연성 회귀 가드: 이상적 사이클엔 고신뢰 추진과 조정이 모두 검출돼야 한다.
    # (정렬 없이 고정 진행하면 추진이 전부 저신뢰로 격하되던 회귀를 막는다.)
    r = elliott.analyze(_many_pivots(), leg_threshold=0.05)
    hi = [s for s in r.segments if s.confidence >= 0.4]
    assert any(s.phase == "motive" for s in hi)  # 고신뢰 추진 존재
    assert any(s.phase == "corrective" for s in hi)  # 고신뢰 조정 존재


def test_filler_blocks_are_low_confidence():
    # 하드룰 미달 '연결용' 블록은 저신뢰(_FILLER_CONF) 이하, 신뢰도는 항상 0~1.
    r = elliott.analyze(_many_pivots(), leg_threshold=0.05)
    assert all(0.0 <= s.confidence <= 1.0 for s in r.segments)
    # 연결용 저신뢰와 고신뢰가 공존(차등이 실제로 일어남).
    confs = [s.confidence for s in r.segments]
    assert min(confs) <= elliott._FILLER_CONF and max(confs) > elliott._FILLER_CONF
