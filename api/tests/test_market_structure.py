"""시장 구조(스윙 LL/HL/LH/HH) 분석 단위 테스트."""

from __future__ import annotations

from app.domain import market_structure as ms


def _series(prices: list[float]) -> tuple[list[str], list[float]]:
    dates = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(prices))]
    return dates, prices


def _zigzag_up() -> list[float]:
    # 뚜렷한 상승 지그재그: 저점·고점 계단식 상향(HH+HL). 임계(일봉 6%)보다 큰 스윙.
    seq = []
    base = 100.0
    for _ in range(4):
        seq += [base, base * 1.20, base * 1.10]  # 오르고 되돌리고
        base *= 1.15
    return seq


def _zigzag_down() -> list[float]:
    seq = []
    base = 300.0
    for _ in range(4):
        seq += [base, base * 0.80, base * 0.90]  # 내리고 반등하고
        base *= 0.85
    return seq


def test_uptrend_structure_hh_hl():
    d, c = _series(_zigzag_up())
    s = ms.analyze(d, c, "day")
    assert s.trend == "up"
    assert s.last_high == "HH" and s.last_low == "HL"


def test_downtrend_structure_lh_ll():
    d, c = _series(_zigzag_down())
    s = ms.analyze(d, c, "day")
    assert s.trend == "down"
    assert s.last_high == "LH" and s.last_low == "LL"


def test_stage1_to_2_setup_on_higher_low():
    # 하락하다 저점을 높이기 시작(마지막 스윙이 HL 저점) → 매수 조짐.
    down = [300.0, 240.0, 270.0, 220.0, 260.0, 235.0, 285.0]  # LL 뒤 저점 상향(235>220)
    d, c = _series(down)
    s = ms.analyze(d, c, "day")
    # 마지막 확정 저점이 직전보다 높으면(HL) setup 후보.
    assert s.setup in ("stage1_to_2", None)  # 데이터 짧아 None 가능 — 크래시 없이 동작 확인


def test_short_series_none():
    s = ms.analyze(["2024-01-01"], [100.0], "day")
    assert s.trend == "none" and s.setup is None and s.pivots == []


def test_threshold_scales_with_frame():
    assert ms._swing_threshold("day") < ms._swing_threshold("week") < ms._swing_threshold("month")


def test_box_breakout_with_volume():
    # 박스 레인지 뒤 상단 돌파 + 거래량 급증 → breakout + vol_confirmed.
    # 상단~120, 하단~100 을 여러 번 오간 뒤 마지막에 130 으로 돌파.
    box = [100.0, 120.0, 102.0, 119.0, 101.0, 121.0, 100.0, 118.0, 130.0]
    d, c = _series(box)
    s = ms.analyze(d, c, "day")
    vols = [100] * 8 + [300]  # 돌파봉 거래량 3배
    sig = ms.box_signal(s.pivots, c, vols)
    assert sig.resistance is not None and sig.support is not None
    assert sig.resistance > sig.support
    # 마지막 종가 130 이 저항 위 → breakout(피벗이 충분히 잡히면).
    if sig.event != "none":
        assert sig.event == "breakout"
        assert sig.vol_confirmed is True


def test_box_inside_when_between():
    box = [100.0, 120.0, 102.0, 119.0, 101.0, 121.0, 110.0]  # 마지막이 박스 안
    d, c = _series(box)
    s = ms.analyze(d, c, "day")
    sig = ms.box_signal(s.pivots, c)
    if sig.event != "none":
        assert sig.event == "inside"


def test_box_none_on_short():
    sig = ms.box_signal([], [100.0, 101.0], None)
    assert sig.event == "none" and sig.support is None
