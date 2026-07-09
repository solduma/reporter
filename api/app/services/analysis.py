"""종목 테크노펀더멘탈 분석 — 성장(린치)·기술(오닐/미너비니)·탑다운(리버모어) 종합.

세 축을 0~100 규칙 스코어로 산출하고(결정적·재현가능), Ollama 키가 있으면 LLM
종합 코멘트를 덧붙인다(키 없으면 스코어만). 지수/프록시 조회는 reporter.us_market 재사용.
"""

from __future__ import annotations

import logging

from reporter import us_market
from reporter.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

_PROXY_LABEL = {".SOX": "미국 반도체", ".IXIC": "미국 기술주", ".INX": "미국 대형주"}


def growth_score(revenue_yoy: float | None, op_yoy: float | None, op_turnaround: bool) -> float | None:
    """성장 점수(0~100). 매출·영업이익 YoY 를 구간 정규화하고 흑자전환 가점.

    데이터가 전무하면 None. YoY 는 -20%~+60% 를 0~1 로 클램프.
    """
    def norm(yoy: float | None) -> float | None:
        if yoy is None:
            return None
        return max(0.0, min((yoy + 0.2) / 0.8, 1.0))

    rev, op = norm(revenue_yoy), norm(op_yoy)
    parts: list[tuple[float, float]] = []
    if rev is not None:
        parts.append((rev, 0.5))
    if op is not None:
        parts.append((op, 0.4))
    if not parts and not op_turnaround:
        return None
    base = sum(v * w for v, w in parts) / sum(w for _, w in parts) if parts else 0.0
    turn = 0.15 if op_turnaround else 0.0
    return round(min(base + turn, 1.0) * 100, 1)


def topdown_score(us_rising: bool | None, kr_rising: bool | None) -> float | None:
    """탑다운 점수(0~100). 미국 프록시(선행 가정, 가중 큼) + 국내 지수 방향.

    각 방향 상승=1.0/보합·불명=0.5/하락=0.0. 둘 다 불명이면 None.
    """
    def dir_val(rising: bool | None) -> float | None:
        if rising is True:
            return 1.0
        if rising is False:
            return 0.0
        return None

    us, kr = dir_val(us_rising), dir_val(kr_rising)
    parts: list[tuple[float, float]] = []
    if us is not None:
        parts.append((us, 0.6))  # 미국 선행 가정 → 가중 큼
    if kr is not None:
        parts.append((kr, 0.4))
    if not parts:
        return None
    return round(sum(v * w for v, w in parts) / sum(w for _, w in parts) * 100, 1)


def overall(scores: list[float | None]) -> float | None:
    """계산된 축들의 단순 평균. 전부 None 이면 None."""
    vals = [s for s in scores if s is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _index_dir(quotes, name: str) -> bool | None:
    for q in quotes:
        if q.name == name:
            return q.rising
    return None


def build_topdown(industry: str | None, market: str | None, session=None) -> tuple[dict, float | None]:
    """미국 프록시 + 국내 지수를 조회해 탑다운 뷰 dict 와 점수를 만든다."""
    proxy_sym = us_market.map_industry_to_proxy(industry, market)
    proxies = us_market.fetch_us_sector_proxies(session)
    kr = us_market.fetch_kr_indices(session)

    proxy_q = next((q for q in proxies if q.name == _PROXY_LABEL.get(proxy_sym)), None)
    kr_ref = "코스닥" if market == "KOSDAQ" else "코스피"
    score = topdown_score(
        proxy_q.rising if proxy_q else None,
        _index_dir(kr, kr_ref),
    )
    view = {
        "us_proxy_name": _PROXY_LABEL.get(proxy_sym, proxy_sym),
        "us_proxy_rising": proxy_q.rising if proxy_q else None,
        "us_proxy_change_ratio": proxy_q.change_ratio if proxy_q else "",
        "kr_indices": [
            {"name": q.name, "change_ratio": q.change_ratio, "rising": q.rising} for q in kr
        ],
    }
    return view, score


_COMMENT_SYSTEM = (
    "너는 테크노펀더멘탈리스트 투자 자문위원이다. 성장(피터 린치)·기술적 추세"
    "(오닐·미너비니)·탑다운(리버모어: 미국 섹터가 국내를 선행) 세 관점의 점수와 지표를 받아 "
    "종목을 3~4문장으로 종합한다. 좋은 점과 리스크를 함께 짚고, 모르면 아는 척하지 않는다. "
    "숫자를 그대로 나열하지 말고 의미를 해석한다."
)


def llm_comment(
    host: str, api_key: str, model: str, stock_name: str, axes: list[dict]
) -> str | None:
    """세 축 점수·지표를 LLM 으로 종합 코멘트. 키 없거나 실패 시 None(스코어만 노출)."""
    if not api_key:
        return None
    lines = [f"종목: {stock_name}"]
    for ax in axes:
        metrics = ", ".join(f"{m['label']} {m['value']}" for m in ax["metrics"])
        lines.append(f"- {ax['label']}: 점수 {ax['score']} ({metrics})")
    try:
        client = OllamaClient(host, api_key)
        return client.chat(model, _COMMENT_SYSTEM, "\n".join(lines), temperature=0.5).strip()
    except Exception as e:
        logger.warning("analysis comment failed for %s: %s", stock_name, e)
        return None
