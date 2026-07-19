"""Forward(예상) 이익 외삽 — 순수 계산 도메인(IO·프레임워크·LLM 모름).

밸류에이션 앵커의 이익 계열(EPS·EBITDA 등)은 기본이 후행(TTM)이라 성장주의 미래 이익이
반영되지 않는다. 컨센서스 추정치는 극소수 종목만 존재하므로, 컨센서스가 없을 때 과거 이익
성장 궤적에서 1년 전방 성장률을 결정론적으로 외삽한다.

성장률 앙상블(최근 1년 성장률을 그대로 쓰면 노이즈·일시요인에 취약하므로 스무딩):
    (1) 과거 3년(최근 12개 YoY) 평균 — 장기 추세 중심
    (2) 가장 최근 YoY — 현재 모멘텀
    (3) 미분+convexity 외삽 — 최근 YoY + 성장률의 1차차분(추세)·½·2차차분(가속/감속)
결합 = 0.4·(1) + 0.3·(2) + 0.3·(3), 이후 [-0.5, +0.6]/yr 로 클립(극단 외삽 방지).

환각 방지: 성장률은 과거 실적에서만 유도되고, 어떤 요소가 어떻게 결합됐는지 메타로 고지한다.
"""

from __future__ import annotations

# 연간 외삽 성장률 클립 범위 — 단일 연도 이익 성장을 상식 범위로 제한(급성장·급감 모두 완충).
_GROWTH_CAP_HIGH = 0.6
_GROWTH_CAP_LOW = -0.5

# 앙상블 가중치 — 3년평균(추세) 0.4, 최근(모멘텀) 0.3, convex 외삽(가속) 0.3.
_W_AVG3Y = 0.4
_W_RECENT = 0.3
_W_CONVEX = 0.3

_MIN_YOY = 3  # YoY 표본이 이보다 적으면 외삽 신뢰 불가 → None(TTM 유지).
_AVG_WINDOW = 12  # 과거 3년 = 12개 분기 YoY.


def _yoy_series(ttm_series: list[float]) -> list[float]:
    """TTM 연환산 이익 시계열에서 YoY 성장률 목록. g_t = E_t/E_{t-4} - 1.

    분기 TTM 창이라 4기 전이 1년 전. 기준(E_{t-4})이 0/음수면 비율이 왜곡돼 건너뛴다(적자 구간).
    """
    out: list[float] = []
    for i in range(4, len(ttm_series)):
        base = ttm_series[i - 4]
        if base > 0:
            out.append(ttm_series[i] / base - 1.0)
    return out


def _convex_extrapolation(yoy: list[float]) -> float:
    """미분+convexity 외삽 = 최근 YoY + slope + ½·curvature.

    slope = 성장률 1차차분(추세 방향), curvature = 2차차분(가속/감속). 테일러 2차 근사로 다음
    기 성장률을 투영한다. 최근 3개 YoY 로 차분을 잡아 노이즈를 억제(더 길면 반응 둔화).
    """
    recent = yoy[-1]
    if len(yoy) < 2:
        return recent
    slope = yoy[-1] - yoy[-2]
    if len(yoy) < 3:
        return recent + slope
    curvature = (yoy[-1] - yoy[-2]) - (yoy[-2] - yoy[-3])
    return recent + slope + 0.5 * curvature


def extrapolate_growth(ttm_series: list[float]) -> tuple[float | None, dict | None]:
    """TTM 연환산 이익 시계열 → 앙상블 1년 전방 성장률과 고지 메타. 표본 부족 시 (None, None).

    ttm_series 는 오래된→최신 순의 TTM 이익(4분기 롤링 합) 목록. 반환 성장률은 소수(0.12=+12%).
    """
    yoy = _yoy_series(ttm_series)
    if len(yoy) < _MIN_YOY:
        return None, None
    avg3y = sum(yoy[-_AVG_WINDOW:]) / len(yoy[-_AVG_WINDOW:])
    recent = yoy[-1]
    convex = _convex_extrapolation(yoy)
    raw = _W_AVG3Y * avg3y + _W_RECENT * recent + _W_CONVEX * convex
    growth = max(_GROWTH_CAP_LOW, min(_GROWTH_CAP_HIGH, raw))
    meta = {
        "growth_pct": round(growth * 100, 2),
        "capped": raw != growth,
        "components": {
            "avg3y_pct": round(avg3y * 100, 2),
            "recent_pct": round(recent * 100, 2),
            "convex_pct": round(convex * 100, 2),
        },
        "yoy_samples": len(yoy),
    }
    return growth, meta
