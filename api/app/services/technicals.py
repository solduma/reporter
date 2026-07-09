"""기술적 지표 계산 — 일봉 시계열에서 오닐/미너비니 스타일 지표 파생.

순수 계산 로직(I/O 없음). 입력은 (close, high, low, volume) 를 가진 일봉 객체의
날짜 오름차순 리스트. 데이터가 부족하면 해당 지표는 None 으로 남긴다(상위에서 결측 처리).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class _Bar(Protocol):
    close: float
    high: float
    low: float
    volume: int


@dataclass
class Technicals:
    last_close: float | None
    high_52w: float | None  # 최근 252거래일 최고가
    near_high_pct: float | None  # 신고가 근접도 (last_close / high_52w, 1.0=신고가)
    ma20: float | None
    ma60: float | None
    ma120: float | None
    ma_aligned: bool | None  # 정배열: close > MA20 > MA60 > MA120
    above_ma120: bool | None
    vol_ratio: float | None  # 최근 거래량 / 최근 50일 평균 거래량
    return_3m: float | None  # 약 63거래일 수익률 %
    trend_score: float | None  # 0~100 종합 기술 점수


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def compute(bars: list[_Bar]) -> Technicals:
    """일봉 리스트(날짜 오름차순)에서 기술 지표를 계산한다."""
    empty = Technicals(None, None, None, None, None, None, None, None, None, None, None)
    if not bars:
        return empty

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    vols = [float(b.volume or 0) for b in bars]
    last = closes[-1]

    window_high = highs[-252:]
    high_52w = max(window_high) if window_high else None
    near_high = (last / high_52w) if high_52w else None

    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    ma120 = _sma(closes, 120)
    # MA 3개가 모두 계산돼야 정배열을 판정한다. 하나라도 없으면 판정 불가(None).
    if ma20 is not None and ma60 is not None and ma120 is not None:
        ma_aligned = last > ma20 > ma60 > ma120
    else:
        ma_aligned = None
    above_ma120 = (last > ma120) if ma120 is not None else None

    # 거래량비: 최근일 거래량 / 직전 50일 평균(최근일 제외). 데이터 부족 시 None.
    vol_ratio = None
    if len(vols) >= 51:
        base = vols[-51:-1]
        avg = sum(base) / len(base)
        vol_ratio = (vols[-1] / avg) if avg > 0 else None

    # 3개월(≈63거래일) 수익률 %.
    return_3m = None
    if len(closes) >= 64:
        past = closes[-64]
        if past > 0:
            return_3m = round((last / past - 1) * 100, 1)

    trend = _trend_score(near_high, ma_aligned, above_ma120, vol_ratio, return_3m)
    return Technicals(
        last_close=round(last, 2),
        high_52w=round(high_52w, 2) if high_52w else None,
        near_high_pct=round(near_high * 100, 1) if near_high else None,
        ma20=round(ma20, 2) if ma20 else None,
        ma60=round(ma60, 2) if ma60 else None,
        ma120=round(ma120, 2) if ma120 else None,
        ma_aligned=ma_aligned,
        above_ma120=above_ma120,
        vol_ratio=round(vol_ratio, 2) if vol_ratio else None,
        return_3m=return_3m,
        trend_score=trend,
    )


def _trend_score(
    near_high: float | None,
    ma_aligned: bool | None,
    above_ma120: bool | None,
    vol_ratio: float | None,
    return_3m: float | None,
) -> float | None:
    """기술 지표를 0~100 점수로 종합. 계산 가능한 항목만 가중 평균한다."""
    parts: list[tuple[float, float]] = []  # (0~1 값, 가중치)
    if near_high is not None:
        # 0.7(고점 대비 -30%)~1.0(신고가) 구간을 0~1로.
        parts.append((max(0.0, min((near_high - 0.7) / 0.3, 1.0)), 0.35))
    if ma_aligned is not None:
        parts.append((1.0 if ma_aligned else (1.0 if above_ma120 else 0.0), 0.30))
    if vol_ratio is not None:
        # 거래량 1.0배=중립(0.5), 2배 이상=최대.
        parts.append((max(0.0, min((vol_ratio - 0.5) / 1.5, 1.0)), 0.15))
    if return_3m is not None:
        # -20%~+40% 구간을 0~1로.
        parts.append((max(0.0, min((return_3m + 20) / 60, 1.0)), 0.20))
    if not parts:
        return None
    total_w = sum(w for _, w in parts)
    score = sum(v * w for v, w in parts) / total_w
    return round(score * 100, 1)
