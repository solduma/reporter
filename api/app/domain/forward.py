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

# 증분마진(Δ영업이익/Δ매출) 클립 — 신규 매출이 이익으로 전이되는 비율의 상식 범위.
_INCR_MARGIN_HIGH = 0.6  # 고정비 레버리지로도 60% 초과 전이는 이례적.
_INCR_MARGIN_LOW = 0.0  # 음수 전이(매출 늘수록 손실)는 forward 상향엔 미적용 → 0 하한.
_MIN_MARGIN_POINTS = 4  # 증분마진 회귀 최소 표본(연간 창).

# PEG 기반 정당 PER — 정당 PER ≈ PEG × 장기성장률(%). PEG=1.5(고성장 프리미엄 허용), 캡 [5,50]배.
_FAIR_PEG = 1.5
_FAIR_PER_CAP_HIGH = 50.0
_FAIR_PER_CAP_LOW = 5.0
_LT_FWD_WEIGHT = 0.5  # 장기 g 결합에서 forward(단기 모멘텀) 가중 — 나머지는 과거 CAGR. 0.5=절충.


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


def _cagr(series: list[float], periods_per_year: int = 4) -> float | None:
    """시계열 시작→끝 CAGR(연복리). 시작값이 0/음수면 None(비율 왜곡). series 는 오래된→최신 순.

    분기 TTM 시계열이면 periods_per_year=4 로 연수 = (len-1)/4. 장기 추세 성장률(지속성장) 근사.
    """
    if len(series) < periods_per_year + 1:
        return None
    start, end = series[0], series[-1]
    if start <= 0 or end <= 0:
        return None
    years = (len(series) - 1) / periods_per_year
    if years <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def long_term_growth(ttm_series: list[float]) -> tuple[float | None, dict | None]:
    """장기 지속성장률 = forward 앙상블 g(단기 모멘텀)와 과거 EPS CAGR(장기 추세)의 감쇄 결합.

    PER 리레이팅용 g 는 '다음 1년'이 아니라 '지속 가능한 장기'여야 하므로, 단기 앙상블 g 를 과거
    CAGR 쪽으로 끌어당겨(감쇄) 극단 모멘텀을 눅인다. CAGR 이 없으면(적자 기저 등) 앙상블 g 만 감쇄.
        g_long = w·g_fwd + (1-w)·g_cagr,  w=_LT_FWD_WEIGHT (없으면 g_fwd 를 감쇄계수로만 축소)
    """
    g_fwd, _fmeta = extrapolate_growth(ttm_series)
    if g_fwd is None:
        return None, None
    g_cagr = _cagr(ttm_series)
    if g_cagr is not None:
        g_long = _LT_FWD_WEIGHT * g_fwd + (1.0 - _LT_FWD_WEIGHT) * g_cagr
    else:
        g_long = g_fwd * _LT_FWD_WEIGHT  # CAGR 없음 — 단기 모멘텀을 보수적으로 감쇄만.
    g_long = max(_GROWTH_CAP_LOW, min(_GROWTH_CAP_HIGH, g_long))
    meta = {
        "g_long_pct": round(g_long * 100, 2),
        "g_forward_pct": round(g_fwd * 100, 2),
        "g_cagr_pct": round(g_cagr * 100, 2) if g_cagr is not None else None,
    }
    return g_long, meta


def fair_per(ttm_series: list[float]) -> tuple[float | None, dict | None]:
    """PEG 기반 정당 PER = PEG × 장기성장률(%). 리레이팅 정량 기준선(soft). 성장률 산출 불가 시 None.

    장기 g(long_term_growth)가 양수일 때만 의미. g≤0 이면 PEG 로 PER 을 논할 수 없어 None.
    캡 [5,50]배로 극단 방지. 반환 메타에 사용한 g·PEG 를 담아 근거를 투명 노출한다.
    """
    g_long, gmeta = long_term_growth(ttm_series)
    if g_long is None or g_long <= 0:
        return None, gmeta
    raw = _FAIR_PEG * (g_long * 100.0)
    capped = max(_FAIR_PER_CAP_LOW, min(_FAIR_PER_CAP_HIGH, raw))
    meta = {"fair_per": round(capped, 1), "peg": _FAIR_PEG,
            "growth_pct": round(g_long * 100, 2), "capped": raw != capped, **(gmeta or {})}
    return round(capped, 1), meta


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


def incremental_margin(rev_ttm: list[float], op_ttm: list[float]) -> tuple[float | None, dict | None]:
    """증분 영업이익률 = ΔOP/ΔRevenue (연 단위 변화의 회귀 기울기). 신규 매출→이익 전이율.

    HITL 신규 매출을 이익으로 환산할 때 쓴다. 단순 영업이익률(OP/Rev)이 아니라 '매출이 늘 때
    이익이 얼마나 붙나'(고정비 레버리지 반영)를 과거 실적의 연간 변화분 회귀 기울기로 잡는다.
    rev_ttm·op_ttm 은 같은 인덱스가 같은 시점인 TTM(4분기 합) 시계열(오래된→최신).

    표본(연간 변화쌍) 부족·분모 0·기울기 음수/과대면 폴백: 최근 단순 영업이익률(양수)로 대체.
    반환 마진은 [0, 0.6] 클립. 둘 다 실패하면 (None, None).
    """
    n = min(len(rev_ttm), len(op_ttm))
    d_rev: list[float] = []
    d_op: list[float] = []
    for i in range(4, n):  # 4분기 전 대비 연간 변화(계절성 제거)
        dr = rev_ttm[i] - rev_ttm[i - 4]
        if dr > 0:  # 매출 증가 구간만(전이율 정의 대상)
            d_rev.append(dr)
            d_op.append(op_ttm[i] - op_ttm[i - 4])
    if len(d_rev) >= _MIN_MARGIN_POINTS:
        slope = sum(d_op) / sum(d_rev)  # ΣΔOP / ΣΔRev — 누적 증분마진(개별비율 평균보다 강건)
        if _INCR_MARGIN_LOW < slope <= _INCR_MARGIN_HIGH:
            return round(slope, 4), {"source": "incremental_regression", "points": len(d_rev),
                                     "margin_pct": round(slope * 100, 2)}
    # 폴백: 최근 단순 영업이익률(양수일 때만).
    if rev_ttm and op_ttm and rev_ttm[-1] > 0:
        simple = op_ttm[-1] / rev_ttm[-1]
        if simple > 0:
            simple = min(simple, _INCR_MARGIN_HIGH)
            return round(simple, 4), {"source": "current_op_margin", "margin_pct": round(simple * 100, 2)}
    return None, None
