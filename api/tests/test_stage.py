"""와인스타인 4국면 분류 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import stage


def _rising(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(n)]


def _falling(n: int, start: float = 300.0, step: float = 1.0) -> list[float]:
    return [start - step * i for i in range(n)]


def _flat(n: int, level: float = 100.0) -> list[float]:
    # 미세 노이즈만 있는 횡보(±0.5% 이내).
    return [level + (0.4 if i % 2 else -0.4) for i in range(n)]


def test_stage2_advancing_price_above_rising_ma():
    # 꾸준한 상승 → 종가가 상승 MA 위 = ② 상승(매수존).
    r = stage.classify(_rising(200), 150)
    assert r.stage == 2
    assert r.label == "② 상승"
    assert r.ma_dir == "rising"
    assert r.price_pos == "above"


def test_stage4_declining_price_below_falling_ma():
    r = stage.classify(_falling(200), 150)
    assert r.stage == 4
    assert r.ma_dir == "falling"
    assert r.price_pos == "below"


def test_stage3_top_flat_after_uptrend():
    # 오래 오른 뒤 (MA 를 평탄화시킬 만큼) 충분히 긴 횡보 → MA 근처+평탄 & 직전 상승세 = ③ 천정.
    closes = _rising(180) + _flat(220, level=280.0)
    r = stage.classify(closes, 150)
    assert r.ma_dir == "flat" and r.price_pos == "near"
    assert r.stage == 3


def test_stage1_base_flat_after_downtrend():
    # 오래 내린 뒤 충분히 긴 횡보 → MA 근처+평탄 & 직전 하락세 = ① 바닥.
    closes = _falling(180, start=300.0) + _flat(220, level=120.0)
    r = stage.classify(closes, 150)
    assert r.ma_dir == "flat" and r.price_pos == "near"
    assert r.stage == 1


def test_insufficient_data_returns_none():
    assert stage.classify(_rising(50), 150).stage is None
    assert stage.classify([], 150).stage is None


def test_segments_merge_and_smooth_flicker():
    # 상승 구간이 이어지면 하나의 ② 구간으로 병합된다.
    closes = _rising(260)
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(260)]
    segs = stage.segments(closes, dates, 150)
    assert segs, "구간이 하나 이상 나와야 한다"
    assert segs[-1]["stage"] == 2  # 마지막은 상승 국면
    # from<=to 순서·연속성.
    for s in segs:
        assert s["from"] <= s["to"]


def test_segments_empty_when_short():
    assert stage.segments(_rising(100), ["2025-01-01"] * 100, 150) == []
