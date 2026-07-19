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

# 앙상블 가중치 — 3년평균(추세) 0.4, 최근(모멘텀) 0.3, convex 외삽(가속) 0.3. 임의 클립 캡은 없다
# (극단은 앙상블 평균이 완충, 하류 fair_per·DCF 의 r-g>0 가드가 최종 방어).
_W_AVG3Y = 0.4
_W_RECENT = 0.3
_W_CONVEX = 0.3

_MIN_YOY = 3  # YoY 표본이 이보다 적으면 외삽 신뢰 불가 → None(TTM 유지).
_AVG_WINDOW = 12  # 과거 3년 = 12개 분기 YoY.

_MIN_MARGIN_POINTS = 4  # 증분마진 회귀 최소 표본(연간 창).

# 정당 PER = 시장 실측 PEG × 실현 CAGR(%). PEG 상수 폴백 없음(실측 결측 시 미산출).
_LT_FWD_WEIGHT = 0.5  # 장기 g 결합에서 forward(단기 모멘텀) 가중 — 나머지는 과거 CAGR. 0.5=절충.


def ttm_windows(quarterly: list[float]) -> list[float]:
    """분기 값 시계열(오래된→최신) → 4분기 롤링 합(TTM) 목록. 분기 미만이면 빈 리스트.

    long_term_growth·extrapolate_growth 의 입력(연환산 시계열). 시장 PEG 배치 등 dict 가 아닌
    순수 값 리스트에서 TTM 창을 만들 때 쓴다.
    """
    return [sum(quarterly[i - 3 : i + 1]) for i in range(3, len(quarterly))]


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
    # 임의 클립 없음 — CAGR 결합이 극단을 완충, 하류 r-g>0 가드가 최종 방어.
    meta = {
        "g_long_pct": round(g_long * 100, 2),
        "g_forward_pct": round(g_fwd * 100, 2),
        "g_cagr_pct": round(g_cagr * 100, 2) if g_cagr is not None else None,
    }
    return g_long, meta


def market_peg(pairs: list[tuple[float, float]]) -> float | None:
    """시장 PEG = 정상 성장 구간 종목의 PER/g% 중앙값. 표본 부족 시 None.

    각 종목 (PER, 장기성장률%) 쌍. PEG 는 '정상 성장주' 개념이라 초고성장(전환기·기저효과)은 정의 밖 —
    성장률 IQR(사분위 [Q1,Q3]) 안의 종목만 남겨 소수 폭발값(성장률 최대 수만%)이 회귀를 지배하지
    않게 한다. 데이터 분포에서 경계를 유도(임의 상수 범위 아님). 남은 종목의 PER/g 비의 중앙값이
    시장 PEG(회귀 기울기보다 이상치에 강건). 양수 PER·양수 g 만.
    """
    xy = [(per, g) for per, g in pairs if per and per > 0 and g and g > 0]
    if len(xy) < 20:  # 최소 표본
        return None
    gs_sorted = sorted(g for _, g in xy)
    q1 = gs_sorted[len(gs_sorted) // 4]
    q3 = gs_sorted[len(gs_sorted) * 3 // 4]
    # 성장률 IQR 안(정상 성장 구간)만. 초고성장·초저성장 제외.
    core = [(per, g) for per, g in xy if q1 <= g <= q3]
    if len(core) < 10:
        return None
    ratios = sorted(per / g for per, g in core)  # PEG = PER / g% (종목별)
    return round(ratios[len(ratios) // 2], 3)  # 중앙값(이상치 강건)


def fair_per(ttm_series: list[float], peg: float | None = None) -> tuple[float | None, dict | None]:
    """PEG 기반 정당 PER = PEG × 실현 EPS CAGR(%). 리레이팅 정량 기준선(soft). 성장률 산출 불가 시 None.

    성장률은 실현 CAGR(추정 아님) — 시장 PEG 도 실현 CAGR 로 구하므로 단위·편향이 일치한다(PEG×g 정합).
    peg 는 시장 횡단면 실측(market_peg)만 사용 — 상수 폴백 없음. peg 결측이면 None(fair_per 미산출).
    임의 캡 없음 — g 실측이라 자연 유계. 메타에 사용한 g·PEG 를 담아 근거를 투명 노출한다.
    """
    if peg is None:  # 시장 PEG 실측 결측 — 상수로 메우지 않고 정당 PER 미산출.
        return None, None
    g = _cagr(ttm_series)  # 실현 EPS CAGR(시장 PEG 와 동일 기준)
    if g is None or g <= 0:
        return None, None
    fair = peg * (g * 100.0)
    meta = {"fair_per": round(fair, 1), "peg": round(peg, 3),
            "peg_source": "market_realized_cagr", "growth_pct": round(g * 100, 2)}
    return round(fair, 1), meta


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
    # 임의 클립 없음 — 3요소 앙상블 평균이 극단을 완충하고, 하류(fair_per·DCF)의 r-g>0 가드가 최종 방어.
    growth = _W_AVG3Y * avg3y + _W_RECENT * recent + _W_CONVEX * convex
    meta = {
        "growth_pct": round(growth * 100, 2),
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
        # 양수 전이만 forward 상향에 반영(음수=매출 늘수록 손실은 미반영). 상한 임의캡 없음(회귀 기울기 그대로).
        if slope > 0:
            return round(slope, 4), {"source": "incremental_regression", "points": len(d_rev),
                                     "margin_pct": round(slope * 100, 2)}
    # 폴백: 최근 단순 영업이익률(양수일 때만).
    if rev_ttm and op_ttm and rev_ttm[-1] > 0:
        simple = op_ttm[-1] / rev_ttm[-1]
        if simple > 0:
            return round(simple, 4), {"source": "current_op_margin", "margin_pct": round(simple * 100, 2)}
    return None, None
