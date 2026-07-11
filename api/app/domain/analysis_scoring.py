"""종목 분석·섹터 수급 스코어링 규칙 — 순수 도메인 로직.

테크노펀더멘탈 분석(성장·탑다운)과 섹터 자금유입(flow) 점수, 섹터 로테이션 점수 등
0~100 결정 규칙을 모은다. 영속화·외부 IO·프레임워크를 모른다(입력은 원시 수치).
정규화 밴드(구간→0~1)는 여러 스코어가 공유하므로 여기 한 곳에 둔다.
"""

from __future__ import annotations


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
def growth_score(revenue_yoy: float | None, op_yoy: float | None, op_turnaround: bool) -> float | None:
    """성장 점수(0~100). 매출·영업이익 YoY 를 -20%~+60% 로 정규화 + 흑자전환 가점.

    데이터가 전무하면 None. 스크리너의 백분위 growth_score(scoring.py)와 달리 절대 구간 기반.
    """
    rev = band(revenue_yoy, -0.2, 0.6)
    op = band(op_yoy, -0.2, 0.6)
    parts: list[tuple[float, float]] = []
    if rev is not None:
        parts.append((rev, 0.5))
    if op is not None:
        parts.append((op, 0.4))
    if not parts and not op_turnaround:
        return None
    base = sum(v * w for v, w in parts) / sum(w for _, w in parts) if parts else 0.0
    turn = 0.15 if op_turnaround else 0.0
    return round(clamp01(base + turn) * 100, 1)


def overall(scores: list[float | None]) -> float | None:
    """계산된 축들의 단순 평균. 전부 None 이면 None."""
    vals = [s for s in scores if s is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


# ── 탑다운 축(수급 섹터 flow) ─────────────────────────────────────────
def topdown_flow_score(
    us_flow: float | None, kr_flow: float | None, kr_index_rising: bool | None
) -> float | None:
    """수급 섹터 flow 기반 탑다운 점수(0~100).

    미국 동일섹터 flow(선행, 가중 큼) + 국내 동일섹터 flow + 국내 지수 방향(보조).
    섹터 flow 를 못 구하면 지수 방향만으로 폴백(계산 가능한 것만 가중 평균).
    """
    parts: list[tuple[float, float]] = []
    if us_flow is not None:
        parts.append((us_flow / 100, 0.45))  # 미국 섹터 선행
    if kr_flow is not None:
        parts.append((kr_flow / 100, 0.40))  # 국내 섹터 수급
    if kr_index_rising is not None:
        parts.append((1.0 if kr_index_rising else 0.0, 0.15))  # 지수 방향 보조
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
