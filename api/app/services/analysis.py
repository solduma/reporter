"""종목 테크노펀더멘탈 분석 — 성장·기술·탑다운 종합.

세 축을 0~100 규칙 스코어로 산출하고(결정적·재현가능), Ollama 키가 있으면 LLM
종합 코멘트를 덧붙인다(키 없으면 스코어만). 지수/프록시 조회는 reporter.us_market 재사용.
"""

from __future__ import annotations

import logging

from app.domain.analysis_scoring import growth_score, overall, topdown_flow_score
from app.ports.llm import LLMPort
from app.services import sector_flow
from reporter import sector_etf, us_market

logger = logging.getLogger(__name__)

# 스코어 규칙은 domain.analysis_scoring 로 이동. 하위호환을 위해 재노출한다.
__all__ = ["build_topdown", "growth_score", "llm_comment", "overall", "topdown_flow_score"]


def _index_dir(quotes, name: str) -> bool | None:
    for q in quotes:
        if q.name == name:
            return q.rising
    return None


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
    "너는 테크노펀더멘탈리스트 투자 자문위원이다. 성장·기술적 추세·"
    "탑다운(미국 섹터가 국내를 선행) 세 관점의 점수와 지표를 받아 "
    "종목을 3~4문장으로 종합한다. 좋은 점과 리스크를 함께 짚고, 모르면 아는 척하지 않는다. "
    "숫자를 그대로 나열하지 말고 의미를 해석한다."
)


def llm_comment(
    llm: LLMPort | None, model: str, stock_name: str, axes: list[dict]
) -> str | None:
    """세 축 점수·지표를 LLM 으로 종합 코멘트. LLM 없거나 실패 시 None(스코어만 노출)."""
    if llm is None:
        return None
    lines = [f"종목: {stock_name}"]
    for ax in axes:
        metrics = ", ".join(f"{m['label']} {m['value']}" for m in ax["metrics"])
        lines.append(f"- {ax['label']}: 점수 {ax['score']} ({metrics})")
    try:
        return llm.chat(model, _COMMENT_SYSTEM, "\n".join(lines), temperature=0.5).strip()
    except Exception as e:
        logger.warning("analysis comment failed for %s: %s", stock_name, e)
        return None
