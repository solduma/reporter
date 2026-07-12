"""종목 테크노펀더멘탈 분석 — 성장·기술·탑다운 종합.

세 축을 0~100 규칙 스코어로 산출하고(결정적·재현가능), Ollama 키가 있으면 LLM
종합 코멘트를 덧붙인다(키 없으면 스코어만). 지수/프록시 조회는 reporter.us_market 재사용.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    "너는 테크노펀더멘탈리스트 투자 자문위원이다. 종목의 성장·기술적 추세·탑다운(미국 섹터가 "
    "국내를 선행) 세 관점 점수에 더해, 오늘의 시장 국면과 최근 리서치·공시(정성 정보)를 함께 받아 "
    "종목을 4~5문장으로 종합한다. (1) 종목 자체의 강점, (2) 리스크·약점, (3) 시장·섹터 맥락이 우호적/"
    "비우호적인지, (4) 매수 전 확인할 점을 짚는다. 숫자를 나열하지 말고 의미를 해석하며, 모르면 "
    "아는 척하지 않는다. 단정적 매수·매도 지시는 하지 않는다."
)


@dataclass
class CommentContext:
    """LLM 종합 코멘트에 주입할 시장 맥락·정성 재료. 없으면(None) 3축만으로 종합."""

    market_phase: str | None = None  # forecast|intraday|closing
    market_summary: str | None = None  # 오늘 시황 요약(앞부분)
    report_count: int = 0  # 최근 리포트 수
    buy_count: int = 0  # 그중 BUY 수
    recent_disclosures: list[str] = field(default_factory=list)  # 최근 공시 제목 몇 건


_PHASE_KO = {"forecast": "개장 전(예상)", "intraday": "장중", "closing": "장 마감 후"}


def llm_comment(
    llm: LLMPort | None,
    model: str,
    stock_name: str,
    axes: list[dict],
    context: CommentContext | None = None,
) -> str | None:
    """세 축 점수·지표 + 시장 맥락·정성 재료를 LLM 으로 종합. LLM 없거나 실패 시 None."""
    if llm is None:
        return None
    lines = [f"종목: {stock_name}", "", "[3축 점수]"]
    for ax in axes:
        metrics = ", ".join(f"{m['label']} {m['value']}" for m in ax["metrics"])
        lines.append(f"- {ax['label']}: 점수 {ax['score']} ({metrics})")
    if context:
        lines.append("")
        lines.append("[시장 맥락]")
        if context.market_phase:
            lines.append(f"- 현재 국면: {_PHASE_KO.get(context.market_phase, context.market_phase)}")
        if context.market_summary:
            lines.append(f"- 오늘 시황: {context.market_summary[:400]}")
        lines.append("")
        lines.append("[최근 리서치·공시]")
        lines.append(f"- 최근 리포트 {context.report_count}건 중 BUY {context.buy_count}건")
        for title in context.recent_disclosures[:3]:
            lines.append(f"- 공시: {title}")
    try:
        return llm.chat(model, _COMMENT_SYSTEM, "\n".join(lines), temperature=0.5).strip()
    except Exception as e:
        logger.warning("analysis comment failed for %s: %s", stock_name, e)
        return None
