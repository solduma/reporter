"""와인스타인 국면 — shape 복합 판별 + 봉단위 리샘플 순수 도메인 단위 테스트."""

from __future__ import annotations

from app.domain import stage


def _rising(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(n)]


def _falling(n: int, start: float = 300.0, step: float = 1.0) -> list[float]:
    return [start - step * i for i in range(n)]


def _flat(n: int, level: float = 100.0) -> list[float]:
    return [level + (0.4 if i % 2 else -0.4) for i in range(n)]


# ma_period=150 로 기존 국면 규칙(일봉 상당)을 재현해 검증.
_MA = 150
_SL = 20


def _classify(closes):
    return stage.classify(closes, _MA, _SL)


def test_stage2_advancing_clean_uptrend():
    # 꾸준한 상승 → 깨끗한 추세(고R²·고ER)+상승 = ② 상승.
    r = _classify(_rising(200))
    assert r.stage == 2
    assert r.label == "② 상승"
    assert r.quality is not None and r.quality > 50  # 직선 상승이라 깨끗함 높음


def test_stage4_declining_clean_downtrend():
    r = _classify(_falling(200))
    assert r.stage == 4


def test_stage3_top_rounding_over_after_uptrend():
    # 오래 오른 뒤 충분히 긴 횡보 → MA 평탄·직전 상승 & 곡률 감속 = ③ 천정.
    r = _classify(_rising(180) + _flat(220, level=280.0))
    assert r.stage == 3


def test_stage1_base_rounding_up_after_downtrend():
    r = _classify(_falling(180, start=300.0) + _flat(220, level=120.0))
    assert r.stage == 1


def test_curvature_separates_base_from_top_in_range():
    # 같은 '레인지'라도 곡률(U자 가속 vs 역U자 감속)이 바닥/천정을 가른다.
    # 하락 감속 후 바닥 다지며 반등 시작(U자).
    base = _falling(120, start=300.0, step=1.0) + _rising(120, start=180.0, step=0.3)
    # 상승 감속 후 천정에서 롤오버(역U자).
    top = _rising(120, start=100.0, step=1.0) + _falling(120, start=220.0, step=0.3)
    assert stage.classify(base, _MA, _SL).stage in (1, 2)  # 바닥/상승초입
    assert stage.classify(top, _MA, _SL).stage in (3, 4)  # 천정/하락초입


def test_insufficient_data_returns_none():
    assert _classify(_rising(50)).stage is None
    assert _classify([]).stage is None


def test_log_slope_r2_clean_vs_noisy():
    # 로그기울기: 상승 양수·하락 음수. R²: 직선 높음, 노이즈 낮음.
    up_slope, up_r2 = stage._log_slope_r2(_rising(60))
    dn_slope, _ = stage._log_slope_r2(_falling(60))
    _, flat_r2 = stage._log_slope_r2(_flat(60))
    assert up_slope > 0 and dn_slope < 0
    assert up_r2 > 0.9
    assert flat_r2 < 0.2


def test_efficiency_ratio_trend_vs_chop():
    assert stage._efficiency_ratio(_rising(60)) > 0.9  # 직선 = 거의 1
    assert stage._efficiency_ratio(_flat(60)) < 0.2  # 조밀 노이즈 = 낮음


def test_resample_closes_weekly_monthly():
    # 2024-01-01(월)~ 20 거래일을 주/월봉 종가(각 구간 마지막)로 리샘플.
    dates = [f"2024-01-{d:02d}" for d in range(1, 21)]  # 1/1~1/20
    closes = [100.0 + i for i in range(20)]
    # day: 그대로
    rd, rc = stage.resample_closes(dates, closes, "day")
    assert rd == dates and rc == closes
    # month: 전부 2024-01 → 1개, 마지막 종가.
    rdm, rcm = stage.resample_closes(dates, closes, "month")
    assert len(rdm) == 1 and rcm[-1] == closes[-1]
    # week: 여러 ISO 주로 쪼개짐(3~4개), 각 주 마지막 종가가 오름차순.
    rdw, rcw = stage.resample_closes(dates, closes, "week")
    assert 2 <= len(rdw) <= 4
    assert rcw == sorted(rcw)  # 종가 오름차순(원 데이터가 증가)


def test_frames_have_expected_bars():
    assert stage.FRAMES["short"].bar == "day" and stage.FRAMES["short"].ma_period == 50
    assert stage.FRAMES["mid"].bar == "week" and stage.FRAMES["mid"].ma_period == 30
    assert stage.FRAMES["long"].bar == "month" and stage.FRAMES["long"].ma_period == 40
    assert stage.FRAMES["long"].slope_lookback == 10  # 장기 기울기창 확장(5→10개월)


def test_resample_volumes_sums_per_bucket():
    dates = [f"2024-01-{d:02d}" for d in range(1, 21)]  # 2024-01, 여러 ISO 주
    vols = [10] * 20
    # month: 전부 1월 → 합 200.
    assert stage.resample_volumes(dates, vols, "month") == [200]
    # week: 주별 합, 총합 보존.
    wk = stage.resample_volumes(dates, vols, "week")
    assert sum(wk) == 200 and len(wk) >= 2
    # day: 그대로.
    assert stage.resample_volumes(dates, vols, "day") == vols


def test_volume_signal_accumulation_vs_distribution():
    # 상승봉에 큰 볼륨=축적, 하락봉에 큰 볼륨=분산. 마지막 봉 제외 확인.
    closes = [100, 101, 100, 102, 101, 103, 102, 104]  # 상승·하락 번갈아, 순상승
    up_heavy = [1, 50, 1, 50, 1, 50, 1, 9999]  # 상승봉 볼륨 큼, 마지막(무시) 초대형
    dn_heavy = [1, 1, 50, 1, 50, 1, 50, 9999]  # 하락봉 볼륨 큼
    assert stage._volume_signal(closes, up_heavy) == "accumulation"
    assert stage._volume_signal(closes, dn_heavy) == "distribution"
    # 볼륨 없음/표본부족 → neutral.
    assert stage._volume_signal(closes, None) == "neutral"
    assert stage._volume_signal([1, 2, 3], [1, 2, 3]) == "neutral"


def test_volume_breaks_range_tie_accumulation_to_base():
    # 곡률·문맥이 애매한 완전 평탄 레인지에서 볼륨이 tiebreak.
    flat = _flat(160, level=100.0)
    up_heavy = [(50 if i % 2 else 1) for i in range(160)]  # 상승봉(홀수 i, +0.4)에 볼륨
    dn_heavy = [(1 if i % 2 else 50) for i in range(160)]
    base = stage.classify(flat, _MA, _SL, up_heavy)
    top = stage.classify(flat, _MA, _SL, dn_heavy)
    # 축적이면 바닥(1) 쪽, 분산이면 천정(3) 쪽으로 갈린다(둘이 달라야 함).
    assert base.volume_signal == "accumulation"
    assert top.volume_signal == "distribution"
    assert base.stage != top.stage


def test_classify_without_volume_still_works():
    # 볼륨 미제공(하위호환) — 종가만으로 판정, volume_signal=neutral.
    r = stage.classify(_rising(200), _MA, _SL)
    assert r.stage == 2
    assert r.volume_signal == "neutral"


def test_segments_merge_and_smooth():
    closes = _rising(260)
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(260)]
    segs = stage.segments(closes, dates, _MA, _SL, min_run=10)
    assert segs
    assert segs[-1]["stage"] == 2  # 마지막은 상승 국면
    for s in segs:
        assert s["from"] <= s["to"]


def test_segments_empty_when_short():
    assert stage.segments(_rising(100), ["2025-01-01"] * 100, _MA, _SL, 10) == []
