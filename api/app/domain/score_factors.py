"""테크노펀더멘탈 축 점수의 '계산 근거' 분해 — 순수 도메인 로직.

각 축(성장·가치·추세·탑다운)의 0~100 점수를, 화면에서 "이 점수가 어떻게 나왔는지"
보여줄 수 있도록 요소별 (라벨·원시값·정규화 기여도·가중치)로 분해한다. 점수 계산 규칙은
analysis_scoring / scoring / technicals 와 동일한 밴드·가중치를 쓰되, 여기서는 각 요소의
정규화값(0~1)과 가중치를 함께 반환해 프론트가 hover 팝업으로 계산식을 노출하게 한다.

영속화·프레임워크를 모른다(입력은 원시 수치). 점수 자체는 기존 함수가 소유하고, 여기서는
동일 규칙으로 '분해'만 한다 — 점수와 분해가 어긋나지 않도록 같은 밴드 상수를 재사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.analysis_scoring import band, clamp01


@dataclass(frozen=True)
class Factor:
    """점수 한 요소의 근거. norm(0~1)*weight 가 이 요소의 점수 기여도."""

    label: str  # 표시명 (예: "매출 YoY")
    value: str  # 원시값 표시 (예: "+32%", "1.4x", "—")
    norm: float | None  # 0~1 정규화값 (계산 불가 시 None → 기여 0)
    weight: float  # 가중치 (같은 축 요소 가중치 합으로 정규화)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "value": self.value,
            "norm": None if self.norm is None else round(self.norm, 3),
            "weight": self.weight,
        }


def _pct(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.{digits}f}%"


def _num(value: float | None, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


# ── 성장 축 분해 (analysis_scoring.growth_score 와 동일 규칙) ──────────────
GROWTH_METHOD = (
    "매출·영업이익 YoY 를 -20%~+60% 구간으로 0~1 정규화 후 가중 평균(매출 0.5·영업익 0.4), "
    "흑자전환이면 +0.15 가점. 데이터 없는 요소는 제외하고 남은 가중치로 재정규화."
)


def growth_factors(
    revenue_yoy: float | None, op_yoy: float | None, op_turnaround: bool
) -> list[Factor]:
    return [
        Factor("매출 YoY", _pct(revenue_yoy), band(revenue_yoy, -0.2, 0.6), 0.5),
        Factor("영업이익 YoY", _pct(op_yoy), band(op_yoy, -0.2, 0.6), 0.4),
        Factor(
            "흑자전환 가점",
            "적용" if op_turnaround else "—",
            1.0 if op_turnaround else None,
            0.15,
        ),
    ]


# ── 가치 축 분해 (scoring.value_score 와 동일 규칙, 절대기준 변형) ─────────
VALUE_METHOD = (
    "저PBR·저PER·저EV/EBITDA 를 후보군 내 저평가 백분위(낮을수록 1)로 환산해 가중 평균"
    "(PBR 0.35·PER 0.28·EV/EBITDA 0.17), 고ROE(15%↑)·고배당(5%↑) 가점."
)


def value_factors(
    per: float | None,
    pbr: float | None,
    ev_ebitda: float | None,
    roe: float | None,
    div_yield: float | None,
    per_rank: float | None,
    pbr_rank: float | None,
    ev_rank: float | None,
) -> list[Factor]:
    """가치 요소 분해. per_rank/pbr_rank/ev_rank 는 저평가 백분위(0~1, 낮을수록 1) — 호출측이
    후보군 랭커로 계산해 넘긴다. 단독(후보군 없음)일 땐 None → 해당 요소 기여 0."""
    roe_norm = None if roe is None else clamp01(roe / 15.0)
    div_norm = None if div_yield is None else clamp01(div_yield / 5.0)
    return [
        Factor("저PBR 백분위", _num(pbr, "배"), pbr_rank, 0.35),
        Factor("저PER 백분위", _num(per, "배"), per_rank, 0.28),
        Factor("저EV/EBITDA 백분위", _num(ev_ebitda, "배"), ev_rank, 0.17),
        Factor("ROE 가점", _num(roe, "%"), roe_norm, 0.12),
        Factor("배당수익률 가점", _num(div_yield, "%"), div_norm, 0.08),
    ]


# ── 추세 축 분해 (technicals._trend_score 와 동일 규칙) ────────────────────
TREND_METHOD = (
    "52주 신고가 근접(70~100%)·이평 정배열·거래량비(0.5~2배)·3개월 수익률(-20~+40%)을 "
    "0~1 정규화해 가중 평균(신고가 0.35·정배열 0.30·수익률 0.20·거래량 0.15)."
)


def trend_factors(
    near_high_pct: float | None,
    ma_aligned: bool | None,
    above_ma120: bool | None,
    vol_ratio: float | None,
    return_3m: float | None,
) -> list[Factor]:
    near_norm = (
        None if near_high_pct is None else clamp01((near_high_pct / 100 - 0.7) / 0.3)
    )
    if ma_aligned is None:
        align_norm: float | None = None
        align_val = "—"
    elif ma_aligned:
        align_norm, align_val = 1.0, "정배열"
    elif above_ma120:
        align_norm, align_val = 1.0, "MA120 위"
    else:
        align_norm, align_val = 0.0, "역배열"
    return [
        Factor("52주 신고가 근접", _num(near_high_pct, "%"), near_norm, 0.35),
        Factor("이평 정배열", align_val, align_norm, 0.30),
        Factor("3개월 수익률", _num(return_3m, "%"), band(return_3m, -20, 40), 0.20),
        Factor("거래량비", _num(vol_ratio, "x", 2), band(vol_ratio, 0.5, 2.0), 0.15),
    ]


# ── 탑다운 축 분해 (analysis_scoring.topdown_flow_score 와 동일 규칙) ──────
TOPDOWN_METHOD = (
    "미국 동일섹터 수급 flow(선행, 0.45)·국내 동일섹터 flow(0.40)·국내 지수 방향(0.15)을 "
    "가중 평균. 섹터 flow 자체가 섹터 ETF 의 추세·신고가·거래량·외국인 수급 종합 0~100 점수."
)


def topdown_factors(
    us_flow: float | None, kr_flow: float | None, kr_index_rising: bool | None
) -> list[Factor]:
    idx_norm = None if kr_index_rising is None else (1.0 if kr_index_rising else 0.0)
    return [
        Factor(
            "미국 섹터 수급(선행)",
            _num(us_flow, "점"),
            None if us_flow is None else us_flow / 100,
            0.45,
        ),
        Factor(
            "국내 섹터 수급",
            _num(kr_flow, "점"),
            None if kr_flow is None else kr_flow / 100,
            0.40,
        ),
        Factor(
            "국내 지수 방향",
            "상승" if kr_index_rising else "하락" if kr_index_rising is False else "—",
            idx_norm,
            0.15,
        ),
    ]


def factors_payload(method: str, factors: list[Factor]) -> dict:
    """축 metrics 에 실을 계산근거 페이로드. 프론트가 hover 팝업으로 표시."""
    return {"method": method, "factors": [f.as_dict() for f in factors]}
