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
    # 임의 클립 없음 — CAGR 결합이 극단을 완충, 하류 r-g>0 가드가 최종 방어.
    meta = {
        "g_long_pct": round(g_long * 100, 2),
        "g_forward_pct": round(g_fwd * 100, 2),
        "g_cagr_pct": round(g_cagr * 100, 2) if g_cagr is not None else None,
    }
    return g_long, meta


def sustainable_growth(ttm_series: list[float]) -> tuple[float | None, dict | None]:
    """순수 장기 성장률 — CAGR 기반, 단기 모멘텀 미혼합. 분모(forward multiple) 전용.

    growth_forward_multiple()의 분모 g로 전달할 성장률. 분자의 g(단기 추정은 이미
    apply_forward_earnings에서 forward_EPS/EV를 산출했으므로, multiple에는 '장기 지속
    성장률'만 써야 분자·분모 g가 이중 적용되지 않는다.

    산출: CAGR(ttm_series). CAGR 결측 시(적자 기반 등) 보수적 폴백으로 g_fwd × 0.5.
    ROE 캡은 growth_forward_multiple() 내부에서 처리하므로 여기선 클립 없음.
    """
    g_cagr = _cagr(ttm_series)
    if g_cagr is not None:
        meta = {
            "source": "cagr", "g_sustainable_pct": round(g_cagr * 100, 2),
            "g_cagr_pct": round(g_cagr * 100, 2),
        }
        return g_cagr, meta
    # CAGR 결측 시: 보수적 폴백으로 단기 앙상블의 절반만 사용
    g_fwd, _ = extrapolate_growth(ttm_series)
    if g_fwd is not None:
        g_fallback = g_fwd * _LT_FWD_WEIGHT
        meta = {
            "source": "extrapolation_fallback",
            "g_sustainable_pct": round(g_fallback * 100, 2),
            "g_forward_pct": round(g_fwd * 100, 2),
            "note": "CAGR 산출 불가 — 단기 성장률의 절반으로 대체(보수)",
        }
        return g_fallback, meta
    return None, None


def growth_forward_multiple(
    fwd_growth: float | None, roe: float | None, coe: float | None,
    terminal_growth: float | None, cap_years: float | None,
) -> tuple[float | None, dict | None]:
    """성장반영 3단계 forward 멀티플(P0/E1) = 배당할인 복리 정의식의 폐형식.

    상대가치 3방식(PER·PBR·EV/EBITDA)의 목표배수를 후행 밴드 대신 이론 forward 배수로 낸다. 배당할인모형
    P0 = Σ_{k=1}^{N} b_k·E0·Π(1+g_j)/(1+r)^k + TV 를 선행이익 E1=E0(1+g1) 로 나눈 것(Damodaran 2/3단계 PE).

    성장률과 배당성향을 독립 변수로 두면 저배당 성장주에서 배수가 5~46배로 요동치므로(payout 딜레마),
    g=ROE×재투자율 회계항등식으로 강제 연동한다: b = max(0, 1 − g/ROE). 재투자율 = 1−b ∈ [0,1] 이므로
    지속가능성장의 수학적 상한은 g ≤ ROE 다 — forward 외삽이 ROE 를 초과하면(재투자만으로 불가능한 성장,
    예 ROE 9% 기업의 EPS 91% 외삽) g 를 ROE 로 캡한다(r−g>0 과 같은 급의 회계 항등식 가드, 임의 상수 아님).
    이 캡이 없으면 g>ROE 에서 b=0 이어도 EPS 복리로 배수가 폭발한다.

    3단계 궤적(모든 파라미터 실측·내생, 임의 상수 없음):
      1) 고성장 n=cap_years 년: g=min(fwd_growth, ROE), ROE=현재, b=max(0,1−g/ROE)
      2) fade n=cap_years 년: g·ROE 선형회귀(g→terminal_growth, ROE→COE)
      3) terminal: g=terminal_growth(국고채10년), ROE=COE(초과수익 소멸) → b=1−g/COE
    발산 방지: g≤ROE 캡 + terminal ROE→COE(초과수익 0 수렴) + terminal_growth<COE 강제(r−g>0 가드).
    ROE·COE·fwd_growth·terminal_growth·cap 결측이거나 terminal_growth≥COE·ROE≤0 이면 (None, 사유) — 스킵.
    """
    if roe is None or roe <= 0 or coe is None or coe <= 0:
        return None, {"reason": "ROE 또는 COE 결측·비양수"}
    if fwd_growth is None or terminal_growth is None or cap_years is None or cap_years <= 0:
        return None, {"reason": "forward 성장·영구성장·CAP 중 결측"}
    if terminal_growth >= coe:  # 고든 발산 가드(r−g>0). terminal_growth 는 국고채10년이라 정상은 <COE.
        return None, {"reason": f"영구성장률({terminal_growth*100:.1f}%)≥COE({coe*100:.1f}%) — terminal 발산"}

    raw_growth = fwd_growth
    fwd_growth = min(fwd_growth, roe)  # 지속가능성장 상한 g≤ROE(재투자율≤1 회계 항등식). 초과 외삽 억제.
    n = round(cap_years)
    pv = 0.0  # Σ 배당 현가 / E0
    eps = 1.0  # E0 기준 상대 EPS(성장 복리 누적)
    disc = 1.0  # (1+r)^k
    # 1단계 — 고성장 n년: g·ROE 고정
    for _ in range(n):
        eps *= 1.0 + fwd_growth
        disc *= 1.0 + coe
        b = max(0.0, 1.0 - fwd_growth / roe)
        pv += b * eps / disc
    # 2단계 — fade n년: g·ROE 선형회귀(g→terminal_growth, ROE→COE)
    for j in range(1, n + 1):
        g = fwd_growth + (terminal_growth - fwd_growth) * j / n
        roe_f = roe + (coe - roe) * j / n
        eps *= 1.0 + g
        disc *= 1.0 + coe
        b = max(0.0, 1.0 - g / roe_f) if roe_f > 0 else 0.0
        pv += b * eps / disc
    # 3단계 — terminal: g=terminal_growth, ROE=COE → b=1−g/COE, 고든 영구가치
    b_term = 1.0 - terminal_growth / coe
    tv = b_term * eps * (1.0 + terminal_growth) / (coe - terminal_growth)
    pv += tv / disc

    fwd_mult = pv / (1.0 + fwd_growth)  # P0/E1
    if fwd_mult <= 0:
        return None, {"reason": "산출 배수 ≤0(초과수익 음수 누적)"}
    meta = {"forward_multiple": round(fwd_mult, 1), "fwd_growth_pct": round(fwd_growth * 100, 2),
            "raw_growth_pct": round(raw_growth * 100, 2), "growth_capped": raw_growth > roe,
            "roe_pct": round(roe * 100, 2), "coe_pct": round(coe * 100, 2),
            "terminal_growth_pct": round(terminal_growth * 100, 2), "cap_years": n,
            "source": "growth_3stage_forward"}
    return round(fwd_mult, 1), meta


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
