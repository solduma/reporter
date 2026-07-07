"""AI 2단계 분석 — 1차 개별 요약, 2차 종합 인사이트."""

from __future__ import annotations

import logging

from .models import Briefing, Report
from .ollama_client import OllamaClient

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "너는 증권사 리포트를 압축 요약하는 애널리스트다. "
    "핵심 주장 1줄, 구체적 수치·종목이 있으면 반드시 포함, 150자 이내로 요약한다. "
    "군더더기 없이 결과만 출력한다."
)

_INSIGHT_SYSTEM = (
    "너는 여러 증권사 리포트를 교차 분석하는 투자 자문위원이다. "
    "단순 나열이 아니라 재해석해서 '오늘 시장에서 뭐가 중요한지'를 전달한다. "
    "좋은 것만 말하지 않고 리스크도 솔직하게 짚는다. 모르면 아는 척하지 않는다."
)

_INSIGHT_TEMPLATE = """아래는 오늘 발행된 증권사 리포트 {count}건의 개별 요약이다.

{summaries}

위 요약들을 교차 분석해 아래 형식으로 한국어 브리핑을 작성해라. 형식과 이모지를 그대로 지켜라.

🔥 오늘의 핵심 (3줄)
→ 여러 리포트에서 겹치거나 임팩트 큰 시장 메시지

📊 주목 테마
→ 반복 언급된 섹터/테마 2~3개, 주목 이유 포함

💎 주목 종목 (최대 5개)
→ 여러 리포트 언급 + 모멘텀 + 수급 흐름 기준 선별

⚠️ 리스크 요인
→ 리포트들이 경고하는 리스크"""


def summarize_reports(client: OllamaClient, model: str, reports: list[Report]) -> list[Report]:
    """1차: 각 리포트를 개별 요약. 실패한 건은 건너뛴다."""
    for r in reports:
        prompt = f"[{r.category}] {r.title} / {r.broker}\n\n{r.text[:4000]}"
        try:
            r.summary = client.chat(model, _SUMMARY_SYSTEM, prompt)
        except Exception as e:
            logger.warning("summary failed for %s: %s", r.label, e)
            r.summary = ""
    return [r for r in reports if r.summary]


def synthesize_insight(client: OllamaClient, model: str, reports: list[Report]) -> Briefing:
    """2차: 모든 요약을 종합해 인사이트 브리핑 생성."""
    lines = [f"- {r.label}\n  {r.summary}" for r in reports]
    prompt = _INSIGHT_TEMPLATE.format(count=len(reports), summaries="\n".join(lines))
    text = client.chat(model, _INSIGHT_SYSTEM, prompt, temperature=0.5)
    return Briefing(
        text=text,
        report_count=len(reports),
        categories=sorted({r.category for r in reports}),
    )
