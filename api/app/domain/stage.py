"""와인스타인 4국면(Stage Analysis) — 지평별 봉단위 + shape 복합 판별. 순수(I/O 없음).

리서치 결론(축1+축2):
- 축1 지평별 봉단위: 단기=일봉 50, 중기=주봉 30(와인스타인 정통 30주선), 장기=월봉 40개월.
  MA 기간과 기울기창을 프레임의 네이티브 봉 기준으로 스케일한다(일봉 20일 고정의 잠복 버그 제거).
  일봉 종가를 도메인에서 주/월봉으로 리샘플해 쓴다(월봉 DB 백필 불필요).
- 축2 shape 복합: 가격 vs MA 위치·MA 기울기(백본)에 더해 로그가격 회귀 기울기+R²(방향·깨끗함),
  Efficiency Ratio(추세/레인지 게이트), 곡률(전·후반 기울기 → 바닥U자 vs 천장역U자)로 판별을 보강한다.
  방향·강도 지표는 Stage 1(바닥)과 3(천정)을 못 나누므로 곡률·직전 문맥으로 가른다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Protocol

# --- 튜너블 파라미터(백테스트로 조정 가능) ---
FLAT_BAND = 0.02  # |기울기창 동안 MA 변화율| 이 이 값 이내면 MA "평평"
PRICE_BAND = 0.03  # |종가/MA - 1| 이 이 값 이내면 가격이 MA "근처"
ER_TREND = 0.30  # Efficiency Ratio 가 이 값 이상이어야 "깨끗한 추세"(미만=횡보)
R2_TREND = 0.50  # 로그가격 회귀 R² 가 이 값 이상이어야 "깨끗한 추세"
CURV_EPS = 0.0005  # 전·후반 로그기울기 차의 절대값이 이 값 초과면 곡률 유의(U자/역U자)

_STAGE_LABELS = {1: "① 바닥", 2: "② 상승", 3: "③ 천정", 4: "④ 하락"}


@dataclass(frozen=True)
class Frame:
    """지평 프레임: 봉단위 + 네이티브 봉 기준 MA기간·기울기창."""

    bar: str  # day | week | month
    ma_period: int  # 네이티브 봉 개수
    slope_lookback: int  # 네이티브 봉 개수
    min_run: int  # 국면 구간 병합 시 깜빡임 흡수 최소 연속 봉


# 지평 → 프레임. 단기 일봉50(≈10주)·중기 주봉30(와인스타인 정통)·장기 월봉40(≈3.3년 Kitchin 순환).
# 장기는 MA 40개월 유지하되 기울기창을 10개월로 넓혀(월봉 데이터 122개월 한계 내) 추세 방향을
# 더 긴 창으로 본다 → 2~10년 지평에 근접(MA 확장은 데이터 부족으로 불가).
FRAMES: dict[str, Frame] = {
    "short": Frame(bar="day", ma_period=50, slope_lookback=10, min_run=10),
    "mid": Frame(bar="week", ma_period=30, slope_lookback=5, min_run=4),
    "long": Frame(bar="month", ma_period=40, slope_lookback=10, min_run=2),
}

# 볼륨 축적/분산 판정 임계 — 상승구간 볼륨/하락구간 볼륨 비율(최근 창). 1 초과=축적, 미만=분산.
VOL_ACCUM = 1.15  # 상승구간 볼륨이 하락구간의 1.15배↑ → 축적(bullish)
VOL_DISTRIB = 0.87  # 상승구간 볼륨이 하락구간의 0.87배↓ → 분산(bearish)
# 변동성 레짐(정규화 ATR 최근/이전 비율) 임계. 수축=바닥(Stage1), 확장=천정/돌파(Stage3).
VOL_CONTRACT = 0.80  # 최근 ATR% 가 이전의 0.80배↓ → 수축(basing)
VOL_EXPAND = 1.25  # 최근 ATR% 가 이전의 1.25배↑ → 확장(climax/breakout)


class _Bar(Protocol):
    close: float


@dataclass
class StageResult:
    stage: int | None  # 1~4, 판정 불가 시 None
    label: str | None  # '② 상승' 등
    ma: float | None  # 해당 프레임 MA 최신값
    ma_dir: str | None  # rising | flat | falling
    price_pos: str | None  # above | near | below
    quality: float | None  # 추세 깨끗함 0~100 (ER·R² 결합) — shape 신뢰도
    volume_signal: str | None  # accumulation | distribution | neutral (축적/분산)
    volatility: str | None  # contraction | expansion | normal (ATR 변동성 레짐)


def resample_closes(
    dates: list[str], closes: list[float], bar: str
) -> tuple[list[str], list[float]]:
    """일봉 (날짜 오름차순, 종가) → 주/월봉 종가로 리샘플한다. 각 주/월의 마지막 거래일 종가.

    dates 는 'YYYY-MM-DD'. bar='day' 면 그대로. 주=ISO 주, 월=역월 기준으로 묶는다.
    """
    if bar == "day":
        return dates, closes
    order: list[tuple] = []
    last: dict[tuple, tuple[str, float]] = {}
    for d, c in zip(dates, closes, strict=True):
        y, m, dd = (int(x) for x in d.split("-"))
        if bar == "month":
            key: tuple = (y, m)
        else:  # week
            iso = date(y, m, dd).isocalendar()
            key = (iso[0], iso[1])
        if key not in last:
            order.append(key)
        last[key] = (d, c)  # 날짜 오름차순이라 마지막 값이 그 주/월의 종가
    rd = [last[k][0] for k in order]
    rc = [last[k][1] for k in order]
    return rd, rc


def resample_volumes(dates: list[str], volumes: list[int], bar: str) -> list[int]:
    """일봉 (날짜 오름차순, 거래량) → 주/월봉 거래량 합으로 리샘플한다. resample_closes 와 정렬 일치.

    주/월봉 거래량은 그 구간의 일봉 거래량 합이다. bar='day' 면 그대로.
    """
    if bar == "day":
        return list(volumes)
    order: list[tuple] = []
    agg: dict[tuple, int] = {}
    for d, v in zip(dates, volumes, strict=True):
        y, m, dd = (int(x) for x in d.split("-"))
        key: tuple = (y, m) if bar == "month" else date(y, m, dd).isocalendar()[:2]
        if key not in agg:
            agg[key] = 0
            order.append(key)
        agg[key] += int(v or 0)
    return [agg[k] for k in order]


@dataclass
class ResampledBars:
    """리샘플된 주/월봉(또는 일봉 그대로). 종가 외 고/저를 보존해 레인지 피처를 계산한다."""

    dates: list[str]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[int]


def _bucket_key(d: str, bar: str) -> tuple:
    y, m, dd = (int(x) for x in d.split("-"))
    return (y, m) if bar == "month" else date(y, m, dd).isocalendar()[:2]


def resample_ohlcv(
    dates: list[str],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[int],
    bar: str,
) -> ResampledBars:
    """일봉 OHLCV → 주/월봉 OHLCV. high=구간 max, low=구간 min, close=마지막, volume=합.

    종가 기반 피처는 resample_closes 와 동일 결과(close=마지막 거래일 종가)를 유지하고,
    고/저를 보존해 ATR·레인지 기반 변동성 레짐을 계산할 수 있게 한다.
    """
    if bar == "day":
        return ResampledBars(list(dates), list(highs), list(lows), list(closes), list(volumes))
    order: list[tuple] = []
    hi: dict[tuple, float] = {}
    lo: dict[tuple, float] = {}
    cl: dict[tuple, tuple[str, float]] = {}
    vol: dict[tuple, int] = {}
    for d, h, low_, c, v in zip(dates, highs, lows, closes, volumes, strict=True):
        k = _bucket_key(d, bar)
        if k not in hi:
            order.append(k)
            hi[k] = h
            lo[k] = low_
            vol[k] = 0
        else:
            hi[k] = max(hi[k], h)
            lo[k] = min(lo[k], low_)
        cl[k] = (d, c)  # 오름차순이라 마지막 종가
        vol[k] += int(v or 0)
    return ResampledBars(
        dates=[cl[k][0] for k in order],
        highs=[hi[k] for k in order],
        lows=[lo[k] for k in order],
        closes=[cl[k][1] for k in order],
        volumes=[vol[k] for k in order],
    )


def _sma_at(closes: list[float], end: int, window: int) -> float | None:
    """closes[end] 를 마지막으로 하는 window SMA(end 포함). 구간 미달이면 None."""
    if end + 1 < window:
        return None
    return sum(closes[end + 1 - window : end + 1]) / window


def _log_slope_r2(values: list[float]) -> tuple[float, float]:
    """로그가격 OLS 회귀 → (봉당 로그기울기, R²). 방향+추세 깨끗함. 양수 종가 필요.

    로그가격은 시간에 선형이면 기울기가 복리 성장률/봉이라 scale-free 하다. R²=추세가
    설명하는 분산 비율(직선에 가까울수록 1). 데이터·분산 부족 시 (0, 0).
    """
    n = len(values)
    if n < 3 or any(v <= 0 for v in values):
        return 0.0, 0.0
    ys = [math.log(v) for v in values]
    mx = (n - 1) / 2  # x = 0..n-1 의 평균
    my = sum(ys) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    sxy = sum((i - mx) * (ys[i] - my) for i in range(n))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy)
    return slope, max(0.0, min(1.0, r2))


def _efficiency_ratio(values: list[float]) -> float:
    """Kaufman Efficiency Ratio = |순변화| / 경로합 ∈ 0~1. 1=직선 이동, 0=조밀 노이즈."""
    if len(values) < 2:
        return 0.0
    net = abs(values[-1] - values[0])
    path = sum(abs(values[i] - values[i - 1]) for i in range(1, len(values)))
    return net / path if path > 0 else 0.0


def _curvature(values: list[float]) -> float:
    """전반부 대비 후반부 로그기울기 변화(가속/감속). >0 볼록(U자·바닥), <0 오목(역U자·천정)."""
    n = len(values)
    if n < 6:
        return 0.0
    mid = n // 2
    s1, _ = _log_slope_r2(values[: mid + 1])
    s2, _ = _log_slope_r2(values[mid:])
    return s2 - s1


def _volume_signal(closes: list[float], volumes: list[int] | None) -> str:
    """축적/분산 판정 — 상승봉 볼륨 vs 하락봉 볼륨 비율(최근 창). 미완성 최신 봉은 제외.

    상승봉 볼륨 우세=축적(bullish), 하락봉 볼륨 우세=분산(bearish). 볼륨 없거나 표본 부족이면 neutral.
    미완성 마지막 봉(부분집계로 볼륨이 비정상적으로 작음)을 빼고 완성 봉만 비교한다.
    """
    if not volumes or len(volumes) != len(closes) or len(closes) < 7:
        return "neutral"
    # 마지막 봉 제외(미완성 가능). 직전까지의 완성 봉 종가·볼륨으로 비교.
    c = closes[:-1]
    v = volumes[:-1]
    up = sum(v[i] for i in range(1, len(c)) if c[i] > c[i - 1])
    dn = sum(v[i] for i in range(1, len(c)) if c[i] < c[i - 1])
    if dn <= 0 or up <= 0:
        return "neutral"
    ratio = up / dn
    if ratio >= VOL_ACCUM:
        return "accumulation"
    if ratio <= VOL_DISTRIB:
        return "distribution"
    return "neutral"


def _volatility_regime(highs: list[float], lows: list[float], closes: list[float]) -> str:
    """정규화 ATR(레인지/종가) 최근 절반 vs 이전 절반 → contraction | expansion | normal.

    수축=베이스 다지기(Stage1 성격), 확장=돌파/천정 클라이맥스(Stage3 성격). 종가만으론 못 보는
    변동성 레짐을 고/저 레인지로 포착한다. 데이터 부족·정보 없음 시 normal.
    """
    n = len(closes)
    if n < 8 or len(highs) != n or len(lows) != n:
        return "normal"
    atrp = [(highs[i] - lows[i]) / closes[i] for i in range(n) if closes[i] > 0]
    if len(atrp) < 8:
        return "normal"
    mid = len(atrp) // 2
    prior = sum(atrp[:mid]) / mid
    recent = sum(atrp[mid:]) / (len(atrp) - mid)
    if prior <= 0:
        return "normal"
    r = recent / prior
    if r <= VOL_CONTRACT:
        return "contraction"
    if r >= VOL_EXPAND:
        return "expansion"
    return "normal"


def _obv_slope(closes: list[float], volumes: list[int] | None) -> float:
    """OBV(누적 방향 거래량) 시계열의 정규화 기울기 부호 재료. 양수=매집(가격에 볼륨 선행)·음수=분산.

    _volume_signal(상승/하락 볼륨비)의 확인용. 여기선 OBV 최근 추세 방향만 반환(+1/-1/0).
    미완성 마지막 봉 제외.
    """
    if not volumes or len(volumes) != len(closes) or len(closes) < 8:
        return 0.0
    c = closes[:-1]
    v = volumes[:-1]
    obv = [0.0]
    for i in range(1, len(c)):
        step = v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0
        obv.append(obv[-1] + step)
    slope, _ = _log_slope_r2([o - min(obv) + 1.0 for o in obv])  # 양수화 후 로그기울기 부호
    return slope


def classify(
    closes: list[float],
    ma_period: int,
    slope_lookback: int,
    volumes: list[int] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> StageResult:
    """종가(날짜 오름차순)로 현재 국면을 shape 복합 판별한다. 데이터 부족 시 stage=None.

    백본(가격 vs MA·MA기울기) + shape(로그회귀 기울기·R²·ER·곡률) + 볼륨(축적/분산·OBV) +
    변동성 레짐(고/저 ATR 수축/확장)으로 판정. highs/lows 를 주면 Stage1(수축)↔Stage3(확장)
    구분을 레인지로 보강한다(종가만으론 못 보는 부분). volumes 는 축적/분산·돌파 확인.
    """
    empty = StageResult(None, None, None, None, None, None, None, None)
    n = len(closes)
    if n < ma_period:
        return empty
    ma_now = _sma_at(closes, n - 1, ma_period)
    if ma_now is None:
        return empty

    last = closes[-1]
    pos_ratio = last / ma_now - 1
    price_pos = "above" if pos_ratio > PRICE_BAND else "below" if pos_ratio < -PRICE_BAND else "near"

    ma_prev = _sma_at(closes, n - 1 - slope_lookback, ma_period)
    if ma_prev is not None and ma_prev > 0:
        slope = ma_now / ma_prev - 1
        ma_dir = "rising" if slope > FLAT_BAND else "falling" if slope < -FLAT_BAND else "flat"
    else:
        ma_dir = "flat"

    # shape 피처: 최근 ma_period 봉(해당 지평 창)에서 회귀·ER·곡률·볼륨·변동성.
    window = closes[-ma_period:]
    win_vol = volumes[-ma_period:] if volumes else None
    lslope, r2 = _log_slope_r2(window)
    er = _efficiency_ratio(window)
    curv = _curvature(window)
    vol_sig = _volume_signal(window, win_vol)
    # OBV 기울기가 볼륨비와 상반되면(다이버전스) 신호를 중립화(거짓 축적/분산 방지).
    obv = _obv_slope(window, win_vol)
    if (vol_sig == "accumulation" and obv < 0) or (vol_sig == "distribution" and obv > 0):
        vol_sig = "neutral"
    volatility = (
        _volatility_regime(highs[-ma_period:], lows[-ma_period:], window)
        if highs and lows
        else "normal"
    )

    stage = _decide(
        price_pos, ma_dir, lslope, r2, er, curv,
        _long_context(closes, ma_period), vol_sig, volatility,
    )
    return StageResult(
        stage=stage,
        label=_STAGE_LABELS.get(stage) if stage else None,
        ma=round(ma_now, 2),
        ma_dir=ma_dir,
        price_pos=price_pos,
        quality=round(er * r2 * 100, 1),
        volume_signal=vol_sig,
        volatility=volatility,
    )


def _long_context(closes: list[float], ma_period: int) -> str:
    """직전 장기 추세: 현재 MA 가 ma_period 전 MA 보다 높으면 'up' 아니면 'down'.

    1(하락 후 바닥)과 3(상승 후 천정)을 가르는 문맥. 과거 MA 없으면 최근 기울기 부호로 폴백.
    """
    n = len(closes)
    ma_now = _sma_at(closes, n - 1, ma_period)
    ma_past = _sma_at(closes, n - 1 - ma_period, ma_period)
    if ma_now is not None and ma_past is not None:
        return "up" if ma_now >= ma_past else "down"
    slope, _ = _log_slope_r2(closes[-ma_period:])
    return "up" if slope >= 0 else "down"


def _decide(
    price_pos: str,
    ma_dir: str,
    slope: float,
    r2: float,
    er: float,
    curv: float,
    long_ctx: str,
    vol_sig: str = "neutral",
    volatility: str = "normal",
) -> int:
    """shape+백본+볼륨+변동성 결합 국면 판정.

    깨끗한 추세(ER·R² 높음)면 기울기 부호로 2/4. 그 외 레인지·라운딩이면 곡률(U자/역U자) → 변동성
    레짐(수축=바닥·확장=천정) → 볼륨(축적=바닥·분산=천정) → 직전 장기 문맥 순으로 가른다.
    가격이 MA 위/아래로 뚜렷하면 백본(추세)이 우선이라 레인지 tiebreak 은 near·평탄 구간에만 쓴다.
    """
    clean = er >= ER_TREND and r2 >= R2_TREND
    # Stage 2: 깨끗한 상승(가격이 MA 아래만 아니면) 또는 상승 MA 위.
    if (clean and slope > 0 and price_pos != "below") or (price_pos == "above" and ma_dir == "rising"):
        return 2
    # Stage 4: 깨끗한 하락(가격이 MA 위만 아니면) 또는 하락 MA 아래.
    if (clean and slope < 0 and price_pos != "above") or (price_pos == "below" and ma_dir == "falling"):
        return 4
    # 레인지·라운딩 → 곡률로 바닥(U자)/천정(역U자).
    if curv > CURV_EPS:
        return 1
    if curv < -CURV_EPS:
        return 3
    # 곡률 미미 → 변동성 레짐(수축=베이스 다지기 Stage1·확장=클라이맥스 Stage3).
    if volatility == "contraction":
        return 1
    if volatility == "expansion":
        return 3
    # 변동성 중립 → 볼륨(축적=바닥/분산=천정), 볼륨도 중립이면 직전 장기 문맥으로.
    if vol_sig == "accumulation":
        return 1
    if vol_sig == "distribution":
        return 3
    return 3 if long_ctx == "up" else 1


def segments(
    closes: list[float], dates: list[str], ma_period: int, slope_lookback: int, min_run: int
) -> list[dict]:
    """각 봉의 국면을 시계열로 계산해 연속 구간으로 병합한다(차트 배경밴드용).

    min_run 미만으로 잠깐 바뀌는 국면은 깜빡임이라 직전 확정 국면으로 흡수한다.
    반환: [{stage, from(date), to(date)}], MA 를 못 구하는 앞 구간은 건너뛴다.
    """
    if len(closes) != len(dates) or len(closes) < ma_period:
        return []

    raw: list[tuple[int, str]] = []
    for i in range(ma_period - 1, len(closes)):
        r = classify(closes[: i + 1], ma_period, slope_lookback)
        if r.stage is not None:
            raw.append((r.stage, dates[i]))
    if not raw:
        return []

    # 짧은 깜빡임(min_run 미만 연속) 흡수: 직전 확정 국면 유지.
    smoothed: list[tuple[int, str]] = []
    run_stage = raw[0][0]
    run_len = 0
    stable = run_stage
    for stage, d in raw:
        if stage == run_stage:
            run_len += 1
        else:
            run_stage = stage
            run_len = 1
        if run_len >= min_run:
            stable = run_stage
        smoothed.append((stable, d))

    out: list[dict] = []
    cur_stage = smoothed[0][0]
    start = smoothed[0][1]
    prev = smoothed[0][1]
    for stage, d in smoothed[1:]:
        if stage != cur_stage:
            out.append({"stage": cur_stage, "from": start, "to": prev})
            cur_stage = stage
            start = d
        prev = d
    out.append({"stage": cur_stage, "from": start, "to": prev})
    return out
