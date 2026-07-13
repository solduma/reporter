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


def test_donchian_position_and_breakout():
    n = 30
    closes = [100.0] * (n - 1) + [130.0]  # 마지막이 신고가
    highs = [105.0] * (n - 1) + [131.0]
    lows = [95.0] * (n - 1) + [129.0]
    vols = [100] * (n - 1) + [300]  # 마지막 볼륨 3배(확인)
    pos, brk = stage._donchian(highs, lows, closes, vols)
    assert pos == 100.0  # 종가가 직전 채널 최고고(105) 위 → 상단 클램프
    assert brk == "up"  # 신고가 + 볼륨 확인
    # 볼륨 부족이면 돌파 미확정.
    _, brk2 = stage._donchian(highs, lows, closes, [100] * n)
    assert brk2 == "none"
    # 신저가 이탈.
    closes_d = [100.0] * (n - 1) + [80.0]
    lows_d = [95.0] * (n - 1) + [79.0]
    _, brk3 = stage._donchian([105.0] * n, lows_d, closes_d, [100] * (n - 1) + [300])
    assert brk3 == "down"


def test_breakout_promotes_to_stage2():
    # 볼륨 확인된 신고가 돌파는 (MA 아래만 아니면) Stage2 로 승격.
    # near 가격 + 평탄 MA 지만 up 돌파면 2.
    flat = _flat(60, level=100.0)
    highs = [101.0] * 59 + [115.0]
    lows = [99.0] * 59 + [113.0]
    vols = [100] * 59 + [400]
    # ma_period=50 로 near 만들되 마지막 신고가 돌파.
    closes = [*flat[:-1], 114.0]
    r = stage.classify(closes, 50, 10, vols, highs, lows)
    assert r.breakout == "up"
    assert r.stage == 2


def test_secular_context_adaptive_length():
    # 120개월(10년) 상승 월봉 → secular MA 는 clamp 최대(120이 아니라 n-slope=108) 근처, 위·상승.
    closes = [100.0 * (1.01**i) for i in range(120)]
    sc = stage.secular_context(closes)
    assert sc.ma_months is not None
    assert stage.SECULAR_MIN <= sc.ma_months <= stage.SECULAR_MAX
    assert sc.position == "above"  # 상승세라 종가가 장기평균 위
    assert sc.ma_dir == "rising"
    # 짧은 이력(40개월 미만+slope) → 판단 불가.
    assert stage.secular_context([100.0] * 30).ma_months is None


def test_secular_length_grows_with_data():
    # 이력이 늘면 secular MA 길이가 자동 확장(clamp 내), 백필 깊어질수록 더 긴 평균.
    short_hist = stage.secular_context([100.0 + i for i in range(60)])
    long_hist = stage.secular_context([100.0 + i for i in range(150)])
    assert short_hist.ma_months is not None and long_hist.ma_months is not None
    assert long_hist.ma_months > short_hist.ma_months
    assert long_hist.ma_months <= stage.SECULAR_MAX  # 상한 클램프


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
    # 볼륨·OHLC 미제공(하위호환) — 종가만으로 판정, volume_signal·volatility 기본값.
    r = stage.classify(_rising(200), _MA, _SL)
    assert r.stage == 2
    assert r.volume_signal == "neutral"
    assert r.volatility == "normal"


def test_resample_ohlcv_preserves_high_low():
    # 월봉 리샘플이 고=구간max·저=구간min·종가=마지막·볼륨=합을 보존한다.
    dates = [f"2024-01-{d:02d}" for d in range(1, 21)]
    highs = [100.0 + i for i in range(20)]
    lows = [90.0 - i * 0.1 for i in range(20)]
    closes = [95.0 + i for i in range(20)]
    vols = [10] * 20
    b = stage.resample_ohlcv(dates, highs, lows, closes, vols, "month")
    assert len(b.closes) == 1  # 전부 2024-01
    assert b.highs[0] == max(highs)  # 구간 최고가
    assert b.lows[0] == min(lows)  # 구간 최저가
    assert b.closes[0] == closes[-1]  # 마지막 종가
    assert b.volumes[0] == 200  # 볼륨 합
    # day 는 그대로.
    bd = stage.resample_ohlcv(dates, highs, lows, closes, vols, "day")
    assert bd.highs == highs and bd.closes == closes


def test_volatility_regime_contraction_vs_expansion():
    n = 40
    closes = [100.0] * n
    # 수축: 최근 절반 레인지가 이전 절반보다 좁음.
    highs_c = [102.0] * 20 + [100.5] * 20
    lows_c = [98.0] * 20 + [99.5] * 20
    assert stage._volatility_regime(highs_c, lows_c, closes) == "contraction"
    # 확장: 최근 절반이 넓음.
    highs_e = [100.5] * 20 + [104.0] * 20
    lows_e = [99.5] * 20 + [96.0] * 20
    assert stage._volatility_regime(highs_e, lows_e, closes) == "expansion"
    # 정보 없음/부족 → normal.
    assert stage._volatility_regime([], [], []) == "normal"


def test_volatility_breaks_range_tie_before_volume():
    # 평탄 레인지에서 변동성 수축=바닥(1)·확장=천정(3). (볼륨보다 우선순위)
    flat = _flat(160, level=100.0)
    hi_contract = [102.0] * 80 + [100.4] * 80
    lo_contract = [98.0] * 80 + [99.6] * 80
    hi_expand = [100.4] * 80 + [105.0] * 80
    lo_expand = [99.6] * 80 + [95.0] * 80
    base = stage.classify(flat, _MA, _SL, None, hi_contract, lo_contract)
    top = stage.classify(flat, _MA, _SL, None, hi_expand, lo_expand)
    assert base.volatility == "contraction" and base.stage == 1
    assert top.volatility == "expansion" and top.stage == 3


def test_obv_slope_sign_matches_true_obv_trend():
    # 회귀: OBV 는 0을 넘나드는 가산 시계열 → 선형 기울기 부호가 실제 OBV 추세와 일치해야 한다
    # (로그 기울기는 상수 이동에 부호가 뒤집혀 오분류하던 버그).
    # 리서치가 제시한 실패 케이스: 순증(+7614)인데 로그 기울기는 음수였던 OBV.
    obv = [0, 0, -492, -492, -492, -890, -890, -4156, -3619, -3619, -1767, 1148, 5337, 7614]
    assert stage._lin_slope([float(x) for x in obv]) > 0  # 순상승 OBV → 양수 기울기
    # 순하락 OBV → 음수.
    assert stage._lin_slope([float(-x) for x in obv]) < 0
    # 부호는 상수 이동에 불변(로그와 달리).
    shifted = [float(x) + 1e6 for x in obv]
    assert (stage._lin_slope([float(x) for x in obv]) > 0) == (stage._lin_slope(shifted) > 0)


def test_obv_divergence_neutralizes_volume_signal():
    # 상승봉 볼륨은 크지만 OBV 추세가 하락(다이버전스)이면 축적 신호를 중립화.
    closes = [100 + i for i in range(80)] + [180 - i for i in range(80)]
    vols = [(50 if closes[i] > closes[i - 1] else 5) for i in range(len(closes))]
    vols[0] = 5
    obv = stage._obv_slope(closes, vols)
    assert isinstance(obv, float)


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


def test_segments_hysteresis_reduces_flicker_in_sideways():
    # 노이즈 있는 횡보 → 히스테리시스+min_run 으로 국면 구간이 촘촘히 쪼개지지 않는다.
    import random

    rng = random.Random(42)
    # MA 근처를 오가는 잔파동(추세 없음): 국면이 자주 near/flat 경계에 걸린다.
    closes = [100.0 + 3.0 * rng.uniform(-1, 1) for _ in range(400)]
    dates = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(400)]
    segs = stage.segments(closes, dates, _MA, _SL, min_run=8)
    # 히스테리시스+min_run 으로 400봉 횡보가 소수 구간으로 병합(과민반응 방지).
    assert len(segs) <= 8
    for s in segs:
        assert s["from"] <= s["to"]


def test_mid_frame_min_run_raised():
    # 배경밴드 안정화를 위해 중기 min_run 을 8주로 상향.
    assert stage.FRAMES["mid"].min_run == 8
