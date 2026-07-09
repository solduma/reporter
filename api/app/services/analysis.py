"""종목 테크노펀더멘탈 분석 — 성장(린치)·기술(오닐/미너비니)·탑다운(리버모어) 종합.

세 축을 0~100 규칙 스코어로 산출하고(결정적·재현가능), Ollama 키가 있으면 LLM
종합 코멘트를 덧붙인다(키 없으면 스코어만). 지수/프록시 조회는 reporter.us_market 재사용.
"""

from __future__ import annotations

import logging

from app.services import sector_flow
from reporter import sector_etf, us_market
from reporter.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


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


def overall(scores: list[float | None]) -> float | None:
    """계산된 축들의 단순 평균. 전부 None 이면 None."""
    vals = [s for s in scores if s is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _index_dir(quotes, name: str) -> bool | None:
    for q in quotes:
        if q.name == name:
            return q.rising
    return None


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
    if not parts:
        return None
    return round(sum(v * w for v, w in parts) / sum(w for _, w in parts) * 100, 1)


def build_topdown(
    theme_names: list[str], market: str | None, session=None
) -> tuple[dict, float | None]:
    """종목 테마 → 국내/미국 섹터 flow(수급) + 국내 지수로 탑다운 뷰·점수를 만든다.

    theme_names 로 대표 국내 섹터를 고르고, 그 섹터의 국내 ETF flow 와 대응 미국
    ETF flow(선행)를 조합한다. 섹터 매칭 실패 시 지수 방향만으로 폴백한다.
    """
    kr_sector = sector_etf.themes_to_kr_sector(theme_names)
    us_sector = sector_etf.kr_sector_to_us(kr_sector)

    kr_flows = {f.sector: f for f in sector_flow.compute_flows("KR", session)}
    us_flows = {f.sector: f for f in sector_flow.compute_flows("US", session)}
    kr_f = kr_flows.get(kr_sector) if kr_sector else None
    us_f = us_flows.get(us_sector) if us_sector else None

    kr_idx = us_market.fetch_kr_indices(session)
    kr_ref = "코스닥" if market == "KOSDAQ" else "코스피"
    score = topdown_flow_score(
        us_f.flow_score if us_f else None,
        kr_f.flow_score if kr_f else None,
        _index_dir(kr_idx, kr_ref),
    )
    view = {
        "kr_sector": kr_sector,
        "kr_sector_flow": kr_f.flow_score if kr_f else None,
        "us_sector": us_sector,
        "us_sector_flow": us_f.flow_score if us_f else None,
        "us_sector_return_3m": us_f.return_3m if us_f else None,
        "kr_indices": [
            {"name": q.name, "change_ratio": q.change_ratio, "rising": q.rising} for q in kr_idx
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
