"""종목 분석·섹터 수급 스코어링 규칙 — 순수 도메인 로직.

테크노펀더멘탈 분석(성장·탑다운)과 섹터 자금유입(flow) 점수, 섹터 로테이션 점수 등
0~100 결정 규칙을 모은다. 영속화·외부 IO·프레임워크를 모른다(입력은 원시 수치).
정규화 밴드(구간→0~1)는 여러 스코어가 공유하므로 여기 한 곳에 둔다.
"""

from __future__ import annotations

import math


def clamp01(value: float) -> float:
    """0~1 로 클램프."""
    return max(0.0, min(value, 1.0))


def band(value: float | None, lo: float, hi: float) -> float | None:
    """[lo, hi] 구간을 0~1 로 선형 정규화(범위 밖은 클램프). None 은 None."""
    if value is None:
        return None
    return clamp01((value - lo) / (hi - lo))


def _weighted(parts: list[tuple[float, float]]) -> float | None:
    """(값0~1, 가중치) 리스트의 가중 평균 → 0~100. 빈 리스트면 None."""
    if not parts:
        return None
    total_w = sum(w for _, w in parts)
    return round(sum(v * w for v, w in parts) / total_w * 100, 1)


# ── 성장 축(종목 분석) ────────────────────────────────────────────────
# 성장 3요소 밴드(절대 구간→0~1). 매출·EPS YoY 는 -20%~+60%. 영업이익 축은 손익상태 4단계 기본점 +
# 영업이익률 증감 pp 연속점(tanh)의 결합(op_profit_norm) — YoY 비율(흑전 시 정의 불가)을 대체한다.
# 가중치: 외형(매출) 최중, 영업이익(내실·방향) 다음, EPS 로 증자 희석 필터. 종목분석·스크리너 공용.
_GROWTH_YOY_BAND = (-0.2, 0.6)
GROWTH_WEIGHTS = {"rev": 0.40, "op": 0.35, "eps": 0.25}

# 손익 상태 4단계 기본점(0~1). 방향(적자→흑자 전환)을 크게 인정, 상태 악화는 강하게 감점.
OP_STATUS_BASE = {"흑자전환": 1.0, "흑자지속": 0.7, "적자전환": 0.3, "적자지속": 0.0}
# 영업이익률 증감 pp → 연속점의 tanh 스케일(k). 분포상 대부분 ±5pp 라 k=0.08(8pp)면 그 구간이
# 민감하게 갈리고 극단(±수십 pp)은 완만히 포화(두꺼운 꼬리에 강건). 선형 밴드의 중앙 뭉침·극단
# 클램프 문제를 함께 해소한다.
_OPM_TANH_K = 0.08
# 상태 기본점과 pp 연속점의 결합 비중. 상태로 큰 방향을, pp 로 규모·변별을 반영.
_OP_STATUS_W = 0.5


def op_margin_pp_score(op_margin_delta: float | None) -> float | None:
    """영업이익률 증감(Δ, 비율) → 0~1 연속점. tanh S-곡선: 0 근처는 민감, ±수십 pp 는 포화.

    Δ=0 → 0.5, +8pp → 0.88, +20pp → 0.98(포화), -8pp → 0.12. None 은 None."""
    if op_margin_delta is None:
        return None
    return 0.5 * (1.0 + math.tanh(op_margin_delta / _OPM_TANH_K))


def op_profit_norm(op_status: str | None, op_margin_delta: float | None) -> float | None:
    """영업이익 축 정규화값(0~1) = 손익상태 기본점 + 영업이익률 증감 pp 연속점의 가중 결합.

    흑전/흑자지속/적자전환/적자지속 4단계로 방향을 주고, tanh pp 점수로 규모·개선폭을 변별한다.
    상태가 없으면(직전 동기 결측) None → 축 제외. pp 결측 시 상태 기본점만으로 폴백.
    """
    if op_status is None:
        return None
    base = OP_STATUS_BASE.get(op_status)
    if base is None:
        return None
    pp = op_margin_pp_score(op_margin_delta)
    if pp is None:
        return base  # pp 결측 → 상태 기본점만
    return _OP_STATUS_W * base + (1.0 - _OP_STATUS_W) * pp


def growth_score(
    revenue_yoy: float | None,
    op_status: str | None,
    op_margin_delta: float | None = None,
    eps_yoy: float | None = None,
) -> float | None:
    """성장 점수(0~100). 매출·EPS YoY + 영업이익(상태+pp) 을 가중 평균(매출 0.4·영업익 0.35·EPS 0.25).

    영업이익 축은 손익상태 4단계 기본점과 영업이익률 증감 pp 연속점(tanh)의 결합이라, 흑전·적전
    구분 없이 방향과 규모를 함께 반영한다. 계산 가능한 요소만 남은 가중치로 재정규화. 전무하면 None.
    스크리너 백분위 성장스코어와 달리 절대 구간 기반.
    """
    w = GROWTH_WEIGHTS
    parts: list[tuple[float, float]] = []
    for norm, weight in (
        (band(revenue_yoy, *_GROWTH_YOY_BAND), w["rev"]),
        (op_profit_norm(op_status, op_margin_delta), w["op"]),
        (band(eps_yoy, *_GROWTH_YOY_BAND), w["eps"]),
    ):
        if norm is not None:
            parts.append((norm, weight))
    if not parts:
        return None
    return round(sum(v * wt for v, wt in parts) / sum(wt for _, wt in parts) * 100, 1)


def overall(scores: list[float | None]) -> float | None:
    """계산된 축들의 단순 평균. 전부 None 이면 None."""
    vals = [s for s in scores if s is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


# ── 가치 축(종목 분석·스크리너 공용) ─────────────────────────────────
# 저평가 절대 정규화 밴드(작을수록 1). 종목분석·스크리너가 동일 점수를 내도록 한 곳에서 소유한다.
# (best↓ = 만점 1.0, worst↑ = 0.0). PER/PBR/EV-EBITDA 모두 낮을수록 저평가.
VALUE_BANDS = {"per": (5.0, 40.0), "pbr": (0.5, 3.0), "ev_ebitda": (3.0, 20.0)}


def cheap_band(value: float | None, best: float, worst: float) -> float | None:
    """저평가 절대 정규화(작을수록 1). 양수만 유효(적자 PER 등 0·음수는 None → 기여 제외).

    후보군 백분위(scoring.cheap_ranker)와 달리 집합에 무관한 절대 구간이라, 어느 화면에서
    보든·필터를 바꿔도 같은 값을 낸다(스크리너 ↔ 종목분석 점수 일치의 핵심).
    """
    if value is None or value <= 0:
        return None
    return clamp01((worst - value) / (worst - best))


def peg(per: float | None, eps_yoy: float | None) -> float | None:
    """PEG = PER / EPS성장률(%). 성장 속도 대비 주가 비율. PER·EPS성장 둘 다 양수일 때만 유효.

    적자(PER≤0)·역성장/정체(eps_yoy≤0)면 PEG 개념이 성립하지 않아 None(가치 축에서 제외).
    """
    if per is None or eps_yoy is None or per <= 0 or eps_yoy <= 0:
        return None
    return round(per / (eps_yoy * 100), 3)


def peg_norm(peg_value: float | None) -> float | None:
    """PEG → 0~1 정규화(낮을수록 1). PEG≤1.0 만점, ≥2.0 은 0(고평가). 리뷰 기준(1.0/1.5/2.0)과 정합.

    None(성장주 아님·적자)은 None → 가치 축 기여 제외(재정규화로 흡수)."""
    if peg_value is None:
        return None
    return clamp01((2.0 - peg_value) / (2.0 - 1.0))


# 가치 축 가중치(합 1). 저PBR·저PER·저EV 저평가 + PEG(성장 대비 저평가) + ROE·배당 가점.
VALUE_WEIGHTS = {"pbr": 0.30, "per": 0.25, "ev": 0.15, "peg": 0.15, "roe": 0.10, "div": 0.05}


def value_score(
    per: float | None,
    pbr: float | None,
    ev_ebitda: float | None,
    roe: float | None,
    div_yield: float | None,
    per_rank: float | None,
    pbr_rank: float | None,
    ev_rank: float | None,
    peg_rank: float | None = None,
) -> float | None:
    """가치 점수(0~100). 저PBR·저PER·저EV/EBITDA 저평가 + PEG(성장 대비) 정규화 + 고ROE·고배당 가점.

    per_rank/pbr_rank/ev_rank/peg_rank 는 저평가 정규화값(0~1, 낮을수록 1) — 호출측이 절대 밴드
    또는 백분위로 넘긴다. None 이면 해당 항목 제외. 절대 가점(ROE 15%↑ 만점, 배당 5%↑ 만점)은
    밴드 없이 clamp. 결측은 재정규화로 흡수.
    """
    w = VALUE_WEIGHTS
    parts: list[tuple[float, float]] = []
    if pbr_rank is not None:
        parts.append((pbr_rank, w["pbr"]))
    if per_rank is not None:
        parts.append((per_rank, w["per"]))
    if ev_rank is not None:
        parts.append((ev_rank, w["ev"]))
    if peg_rank is not None:
        parts.append((peg_rank, w["peg"]))
    if roe is not None:
        parts.append((clamp01(roe / 15.0), w["roe"]))
    if div_yield is not None:
        parts.append((clamp01(div_yield / 5.0), w["div"]))
    if not parts:
        return None
    return round(sum(v * wt for v, wt in parts) / sum(wt for _, wt in parts) * 100, 1)


def value_score_abs(
    per: float | None,
    pbr: float | None,
    ev_ebitda: float | None,
    roe: float | None,
    div_yield: float | None,
    eps_yoy: float | None = None,
) -> tuple[float | None, tuple[float | None, float | None, float | None, float | None]]:
    """절대 밴드 기반 가치 점수 + (per_norm, pbr_norm, ev_norm, peg_norm). 종목분석·스크리너 공용.

    반환한 norm 4튜플은 score_factors 분해에 그대로 넘겨 점수와 근거가 어긋나지 않게 한다.
    PEG 는 per·eps_yoy 로 산출(성장주만 유효), 나머지는 절대 밴드 저평가 정규화.
    """
    per_r = cheap_band(per, *VALUE_BANDS["per"])
    pbr_r = cheap_band(pbr, *VALUE_BANDS["pbr"])
    ev_r = cheap_band(ev_ebitda, *VALUE_BANDS["ev_ebitda"])
    peg_r = peg_norm(peg(per, eps_yoy))
    score = value_score(per, pbr, ev_ebitda, roe, div_yield, per_r, pbr_r, ev_r, peg_r)
    return score, (per_r, pbr_r, ev_r, peg_r)


# ── 탑다운 축(수급 섹터 flow + 종목 수급) ────────────────────────────
def topdown_flow_score(
    us_flow: float | None,
    kr_flow: float | None,
    kr_index_flow: float | None,
    stock_rs: float | None = None,
) -> float | None:
    """수급 섹터 flow + 종목 수급 기반 탑다운 점수(0~100).

    미국 동일섹터 flow(선행, 가중 큼) + 국내 동일섹터 flow + 국내 지수 수급(보조) + 종목 자체
    상대강도(RS, 종목 수급). 앞 세 항은 섹터 로테이션(같은 섹터면 동일), stock_rs 는 종목별로
    달라 같은 섹터 안에서도 변별한다(섹터 점수가 ~22종류로 뭉치던 문제 보정). 계산 가능한 것만
    가중 평균 — stock_rs 만 있으면(섹터 미분류) 그것만으로, 섹터만 있으면 섹터만으로 폴백.
    """
    parts: list[tuple[float, float]] = []
    if us_flow is not None:
        parts.append((us_flow / 100, 0.35))  # 미국 섹터 선행
    if kr_flow is not None:
        parts.append((kr_flow / 100, 0.30))  # 국내 섹터 수급
    if kr_index_flow is not None:
        parts.append((kr_index_flow / 100, 0.10))  # 국내 지수 수급(보조)
    if stock_rs is not None:
        parts.append((clamp01(stock_rs / 100), 0.25))  # 종목 상대강도(RS) — 종목별 변별
    return _weighted(parts)


# ── 섹터 자금유입(flow) ───────────────────────────────────────────────
def flow_score(
    return_3m: float | None,
    near_high_pct: float | None,
    vol_ratio: float | None,
    foreign_delta: float | None,
) -> float | None:
    """섹터 ETF 기술 지표를 0~100 자금유입 스코어로. 계산 가능한 항목만 가중 평균.

    return_3m -20%~+40%, near_high 70%~100%, vol_ratio 0.5~2배, foreign_delta -1pp~+1pp.
    """
    parts: list[tuple[float, float]] = []
    r = band(return_3m, -20, 40)
    if r is not None:
        parts.append((r, 0.40))  # 추세가 핵심 가중
    if near_high_pct is not None:
        # 70%~100% 근접 → 0~1. 리터럴 0.3 나눗셈으로 부동소수 결과를 레거시와 동일하게 유지.
        parts.append((clamp01((near_high_pct / 100 - 0.7) / 0.3), 0.30))  # 신고가권일수록 주도
    vr = band(vol_ratio, 0.5, 2.0)
    if vr is not None:
        parts.append((vr, 0.20))  # 관심 유입
    fd = band(foreign_delta, -1.0, 1.0)
    if fd is not None:
        parts.append((fd, 0.10))  # 국내 전용 외국인 수급
    return _weighted(parts)


def foreign_delta(foreign_ratios: list[float | None], lookback: int = 20) -> float | None:
    """외국인 보유율의 최근 변화(pp). 최신 - lookback거래일 전. 데이터 부족 시 None."""
    vals = [(i, r) for i, r in enumerate(foreign_ratios) if r is not None]
    if len(vals) < 2:
        return None
    last_i, last = vals[-1]
    prior = next((r for i, r in reversed(vals) if i <= last_i - lookback), vals[0][1])
    return round(last - prior, 2)


# ── 섹터 로테이션(리서치 관점) ────────────────────────────────────────
def rotation_score(avg_sentiment: float, report_count: int, max_count: int) -> float:
    """섹터 로테이션 점수(0~100). 센티먼트(-1~1→0~1) 70% + 커버리지 비중 30%.

    연산 결합순서를 레거시(`0.7*(avg+1)/2 + 0.3*count/max_count`)와 그대로 유지해 부동소수
    결과를 보존한다(항 그룹핑을 바꾸면 마지막 소수 자리가 달라진다). max_count 0 은 방어.
    """
    if not max_count:
        return round(0.7 * (avg_sentiment + 1) / 2 * 100, 1)
    return round((0.7 * (avg_sentiment + 1) / 2 + 0.3 * report_count / max_count) * 100, 1)


# ── 분류 규칙(임계값 정책) ────────────────────────────────────────────
def flow_strength(score: float | None) -> str | None:
    """자금유입 강도(0~100)를 등급으로 분류(임계 60/40). None 은 None(표시는 호출측 책임).

    한글 라벨·포매팅 같은 표현(presentation)은 도메인 밖(라우터 edge)에서 한다.
    """
    if score is None:
        return None
    return "strong" if score >= 60 else "moderate" if score >= 40 else "weak"


# 센티먼트 → 수치(시계열 평균 산출용). BUY +1, HOLD 0, SELL -1.
SENTIMENT_SCORE = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}
