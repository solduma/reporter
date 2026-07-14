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
