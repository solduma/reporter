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

from app.domain.analysis_scoring import (
    GROWTH_WEIGHTS,
    VALUE_WEIGHTS,
    band,
    clamp01,
    margin_pp_score,
    status_norm,
)


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
    "매출 YoY 를 -20%~+60% 로 정규화하고, 영업이익·순이익·EBITDA 는 각각 손익상태(규모·방향: 흑전 "
    "1.0·흑자지속 0.7·적자전환 0.3·적자지속 0)와 마진율 증감 pp(수익성, tanh)를 독립 요소로 본다. "
    "가중 평균 — 매출 0.24, 각 이익 상태:마진 ≈ 0.16:0.14(영업)·0.13:0.11(순)·0.12:0.10(EBITDA). "
    "결측 요소는 제외하고 재정규화."
)

# 이익률 증감(pp) 표시. 부호와 함께 pp 로 보여준다.
def _pp(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}pp"


def growth_factors(
    revenue_yoy: float | None,
    op_status: str | None,
    op_margin_delta: float | None = None,
    net_status: str | None = None,
    net_margin_delta: float | None = None,
    ebitda_status: str | None = None,
    ebitda_margin_delta: float | None = None,
) -> list[Factor]:
    # 각 이익을 상태(규모·방향)와 마진율 증감(수익성) 두 요소로 분리 — 마진 이중계산 없이 명시 노출.
    w = GROWTH_WEIGHTS
    return [
        Factor("매출 YoY", _pct(revenue_yoy), band(revenue_yoy, -0.2, 0.6), w["rev"]),
        Factor("영업이익", op_status or "—", status_norm(op_status), w["op_status"]),
        Factor("영업이익률(OPM) 증감", _pp(op_margin_delta), margin_pp_score(op_margin_delta), w["op_margin"]),
        Factor("순이익", net_status or "—", status_norm(net_status), w["net_status"]),
        Factor("순이익률(NPM) 증감", _pp(net_margin_delta), margin_pp_score(net_margin_delta), w["net_margin"]),
        Factor("EBITDA", ebitda_status or "—", status_norm(ebitda_status), w["ebitda_status"]),
        Factor("EBITDA마진 증감", _pp(ebitda_margin_delta), margin_pp_score(ebitda_margin_delta), w["ebitda_margin"]),
    ]


# ── 가치 축 분해 (analysis_scoring.value_score 와 동일 규칙) ─────────
VALUE_METHOD = (
    "저PBR·저PER·저EV/EBITDA 를 저평가 정규화(낮을수록 1)하고, PEG(PER/EPS성장률, ≤1 만점~≥2 는 0)로 "
    "성장 대비 저평가를 함께 본다. 가중 평균(PBR 0.30·PER 0.25·EV/EBITDA 0.15·PEG 0.15) + "
    "고ROE(15%↑)·고배당(5%↑) 가점. 결측 요소는 제외하고 남은 가중치로 재정규화."
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
    peg_rank: float | None = None,
    peg_value: float | None = None,
    peg_surrogate_status: str | None = None,
) -> list[Factor]:
    """가치 요소 분해. per_rank/pbr_rank/ev_rank/peg_rank 는 저평가 정규화값(0~1, 낮을수록 1) —
    호출측이 절대 밴드/백분위로 계산해 넘긴다. 단독(후보군 없음)일 땐 None → 해당 요소 기여 0.

    peg_value 가 없어도 peg_rank 가 있으면(흑자전환 등 EPS YoY 불가 → 순이익률 대체점) PEG 표시값을
    상태 라벨(peg_surrogate_status)로 채워 점수 기여와 근거가 어긋나지 않게 한다.
    """
    w = VALUE_WEIGHTS
    roe_norm = None if roe is None else clamp01(roe / 15.0)
    div_norm = None if div_yield is None else clamp01(div_yield / 5.0)
    if peg_value is not None:
        peg_display = _num(peg_value, "", 2)
    elif peg_rank is not None and peg_surrogate_status:
        peg_display = peg_surrogate_status  # 대체점(흑자전환/흑자지속) — 수치 대신 상태
    else:
        peg_display = "—"
    return [
        Factor("저PBR", _num(pbr, "배"), pbr_rank, w["pbr"]),
        Factor("저PER", _num(per, "배"), per_rank, w["per"]),
        Factor("저EV/EBITDA", _num(ev_ebitda, "배"), ev_rank, w["ev"]),
        Factor("PEG", peg_display, peg_rank, w["peg"]),
        Factor("ROE 가점", _num(roe, "%"), roe_norm, w["roe"]),
        Factor("배당수익률 가점", _num(div_yield, "%"), div_norm, w["div"]),
    ]


# ── 추세 축 분해 (technicals._trend_score 와 동일 규칙) ────────────────────
TREND_METHOD = (
    "52주 신고가 근접(70~100%)·이평 정배열·3개월 수익률(-20~+40%)·거래량비(0.5~2배)·"
    "와인스타인 국면을 0~1 정규화해 가중 평균(신고가 0.35·정배열 0.30·수익률 0.20·거래량 0.15·"
    "국면 0.15, 계산 가능한 항목 합으로 재정규화). 국면: 상승1.0·바닥0.5·천정0.3·하락0."
)


_STAGE_LABEL = {1: "① 바닥", 2: "② 상승", 3: "③ 천정", 4: "④ 하락"}
_STAGE_NORM = {2: 1.0, 1: 0.5, 3: 0.3, 4: 0.0}


def trend_factors(
    near_high_pct: float | None,
    ma_aligned: bool | None,
    above_ma120: bool | None,
    vol_ratio: float | None,
    return_3m: float | None,
    stage: int | None = None,
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
    factors = [
        Factor("52주 신고가 근접", _num(near_high_pct, "%"), near_norm, 0.35),
        Factor("이평 정배열", align_val, align_norm, 0.30),
        Factor("3개월 수익률", _num(return_3m, "%"), band(return_3m, -20, 40), 0.20),
        Factor("거래량비", _num(vol_ratio, "x", 2), band(vol_ratio, 0.5, 2.0), 0.15),
    ]
    if stage is not None:
        factors.append(
            Factor("와인스타인 국면", _STAGE_LABEL.get(stage, "—"), _STAGE_NORM.get(stage), 0.15)
        )
    return factors


# ── 탑다운 축 분해 (analysis_scoring.topdown_flow_score 와 동일 규칙) ──────
TOPDOWN_METHOD = (
    "미국 동일섹터 수급 flow(선행, 0.35)·국내 동일섹터 flow(0.30)·국내 지수 수급(0.10)·"
    "종목 상대강도 RS(0.25)를 가중 평균. 섹터 세 항은 추세·신고가·거래량(+외국인) 종합 0~100 수급 "
    "점수(같은 섹터면 동일), RS 는 종목별로 달라 같은 섹터 안에서도 종목을 변별한다."
)


def topdown_factors(
    us_flow: float | None,
    kr_flow: float | None,
    kr_index_flow: float | None,
    stock_rs: float | None = None,
) -> list[Factor]:
    return [
        Factor(
            "미국 섹터 수급(선행)",
            _num(us_flow, "점"),
            None if us_flow is None else us_flow / 100,
            0.35,
        ),
        Factor(
            "국내 섹터 수급",
            _num(kr_flow, "점"),
            None if kr_flow is None else kr_flow / 100,
            0.30,
        ),
        Factor(
            "국내 지수 수급",
            _num(kr_index_flow, "점"),
            None if kr_index_flow is None else kr_index_flow / 100,
            0.10,
        ),
        Factor(
            "종목 상대강도(RS)",
            "—" if stock_rs is None else f"{int(stock_rs)}",
            None if stock_rs is None else clamp01(stock_rs / 100),
            0.25,
        ),
    ]


def factors_payload(method: str, factors: list[Factor]) -> dict:
    """축 metrics 에 실을 계산근거 페이로드. 프론트가 hover 팝업으로 표시."""
    return {"method": method, "factors": [f.as_dict() for f in factors]}
