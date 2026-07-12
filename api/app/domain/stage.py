"""와인스타인 4국면(Stage Analysis) — 순수 규칙 분류.

Stan Weinstein 의 30주 이동평균 기반 국면을 일봉 시계열에서 결정론적으로 분류한다.
1=바닥/매집, 2=상승/마크업(매수존), 3=천정/분산, 4=하락/마크다운(회피). 정통 기준은
주봉 30주 MA ≈ 일봉 150일 MA. 단기(50)·중기(150)·장기(200) 프레임에 같은 규칙을 얹는다.

가격 vs MA 위치와 MA 기울기만으론 1(바닥)과 3(천정)이 동일(둘 다 flat+near)하므로,
직전 장기 추세 문맥(최근 MA 가 올랐나 내렸나)으로 가른다. I/O 없음(순수).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# 튜너블 파라미터. 기울기·평탄 밴드는 노이즈와 균형을 맞춘 초기값(백테스트로 조정 가능).
SLOPE_LOOKBACK = 20  # MA 기울기 측정 구간(거래일)
FLAT_BAND = 0.02  # |20봉 MA 변화율| 이 이 값 이내면 "평평"
PRICE_BAND = 0.03  # |종가/MA - 1| 이 이 값 이내면 "MA 근처"

_STAGE_LABELS = {1: "① 바닥", 2: "② 상승", 3: "③ 천정", 4: "④ 하락"}
# 프레임별 대표 MA 기간(단기·중기(정통 Weinstein)·장기).
FRAME_PERIODS = {"short": 50, "mid": 150, "long": 200}


class _Bar(Protocol):
    close: float


@dataclass
class StageResult:
    stage: int | None  # 1~4, 판정 불가 시 None
    label: str | None  # '② 상승' 등
    ma: float | None  # 해당 프레임 MA 최신값
    ma_dir: str | None  # rising | flat | falling
    price_pos: str | None  # above | near | below


def _sma_at(closes: list[float], end: int, window: int) -> float | None:
    """closes[end] 를 마지막으로 하는 window SMA(end 포함). 구간 미달이면 None."""
    if end + 1 < window:
        return None
    return sum(closes[end + 1 - window : end + 1]) / window


def classify(closes: list[float], ma_period: int) -> StageResult:
    """종가(날짜 오름차순)와 MA 기간으로 현재 와인스타인 국면을 분류한다.

    데이터가 MA 기간 + 기울기 측정 구간만큼 없으면 stage=None.
    """
    empty = StageResult(None, None, None, None, None)
    n = len(closes)
    if n < ma_period:
        return empty

    ma_now = _sma_at(closes, n - 1, ma_period)
    if ma_now is None:
        return empty

    last = closes[-1]
    pos_ratio = last / ma_now - 1
    price_pos = "above" if pos_ratio > PRICE_BAND else "below" if pos_ratio < -PRICE_BAND else "near"

    # MA 기울기: SLOPE_LOOKBACK 봉 전 MA 대비 변화율. 그만큼의 과거 MA 가 없으면 flat 로 본다.
    ma_prev = _sma_at(closes, n - 1 - SLOPE_LOOKBACK, ma_period)
    if ma_prev is not None and ma_prev > 0:
        slope = ma_now / ma_prev - 1
        ma_dir = "rising" if slope > FLAT_BAND else "falling" if slope < -FLAT_BAND else "flat"
    else:
        ma_dir = "flat"

    stage = _stage_from(price_pos, ma_dir, _long_context(closes, ma_period))
    return StageResult(
        stage=stage,
        label=_STAGE_LABELS.get(stage) if stage else None,
        ma=round(ma_now, 2),
        ma_dir=ma_dir,
        price_pos=price_pos,
    )


def _long_context(closes: list[float], ma_period: int) -> str:
    """직전 장기 추세: 현재 MA 가 ma_period 전 MA 보다 높으면 'up' 아니면 'down'.

    1(하락 후 바닥)과 3(상승 후 천정)을 구분하는 문맥. 과거 MA 가 없으면 최근 MA 기울기로 폴백.
    """
    n = len(closes)
    ma_now = _sma_at(closes, n - 1, ma_period)
    ma_past = _sma_at(closes, n - 1 - ma_period, ma_period)
    if ma_now is not None and ma_past is not None:
        return "up" if ma_now >= ma_past else "down"
    # 폴백: SLOPE_LOOKBACK 기울기 부호.
    ma_prev = _sma_at(closes, n - 1 - SLOPE_LOOKBACK, ma_period)
    if ma_now is not None and ma_prev is not None:
        return "up" if ma_now >= ma_prev else "down"
    return "up"


def _stage_from(price_pos: str, ma_dir: str, long_ctx: str) -> int:
    """가격 위치·MA 방향·장기 문맥에서 국면 판정.

    2(상승)=MA 위+상승/평탄, 4(하락)=MA 아래+하락/평탄. 경계(near)는 장기 문맥으로 1/3 구분.
    """
    if price_pos == "above" and ma_dir in ("rising", "flat"):
        return 2
    if price_pos == "below" and ma_dir in ("falling", "flat"):
        return 4
    if price_pos == "above":  # 가격은 위인데 MA 하락 → 상승 후 꺾임 = 천정
        return 3
    if price_pos == "below":  # 가격은 아래인데 MA 상승 → 하락 후 반등 시도 = 바닥
        return 1
    # near: MA 근처 횡보 → 직전이 상승세였으면 천정(3), 하락세였으면 바닥(1).
    return 3 if long_ctx == "up" else 1


def segments(closes: list[float], dates: list[str], ma_period: int, min_run: int = 10) -> list[dict]:
    """각 봉의 국면을 시계열로 계산해 연속 구간으로 병합한다(차트 배경밴드용).

    min_run 미만으로 잠깐 바뀌는 국면은 깜빡임이라 직전 확정 국면으로 흡수한다. 와인스타인
    국면은 주(週) 단위로 느리게 바뀌므로 기본 10봉(≈2주)으로 경계 노이즈를 눌러 배경을 읽기 쉽게 한다.
    반환: [{stage, from(date), to(date)}], MA 를 못 구하는 앞 구간은 건너뛴다.
    """
    if len(closes) != len(dates) or len(closes) < ma_period:
        return []

    raw: list[tuple[int, str]] = []  # (stage, date) — MA 계산 가능한 지점부터
    for i in range(ma_period - 1, len(closes)):
        window = closes[: i + 1]
        r = classify(window, ma_period)
        if r.stage is not None:
            raw.append((r.stage, dates[i]))
    if not raw:
        return []

    # 짧은 깜빡임(min_run 미만 연속) 흡수: 직전 확정 국면을 유지한다.
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

    # 연속 동일 국면을 구간으로 병합.
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
