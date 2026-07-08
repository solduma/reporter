"""AI 2단계 분석 — 1차 개별 요약, 2차 종합 인사이트."""

from __future__ import annotations

import logging
import re

from .models import Briefing, DigestResult, Report
from .ollama_client import OllamaClient
from .selector import select_top

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


_ENTITY_SYSTEM = (
    "너는 한 종목(또는 산업)에 대해 나온 여러 증권사 리포트를 종합하는 애널리스트다. "
    "핵심 컨센서스와 이견을 함께 짚어 3~4줄로 종합한다. 목표가·투자의견 수치가 있으면 포함. "
    "모르면 아는 척하지 않는다. 결과 본문만 출력한다."
)

_DIGEST_SYSTEM = (
    "너는 특정 분야 증권사 리포트를 교차 분석하는 투자 자문위원이다. "
    "모든 리포트를 참고해 '지금 이 분야에서 뭐가 중요한지'를 장문(6~10줄)으로 종합한다. "
    "단순 나열이 아니라 재해석하고, 리스크도 솔직히 짚는다. 모르면 아는 척하지 않는다.\n"
    "본문을 다 쓴 뒤 반드시 마지막 줄에 실제로 근거로 삼은 소스를 인용 빈도(많이 참고한) 순으로 "
    "최대 5개, 정확히 이 형식으로 출력한다: SOURCES: S3,S7,S1"
)

_SOURCES_RE = re.compile(r"^SOURCES:\s*(.+)$", re.MULTILINE)


def synthesize_entity(client: OllamaClient, model: str, reports: list[Report]) -> str:
    """한 종목/산업에 대한 여러 요약을 하나의 종합 요약으로 합친다."""
    if len(reports) == 1:
        return reports[0].summary
    lines = [f"- [{r.broker}] {r.summary}" for r in reports]
    prompt = "다음은 같은 대상에 대한 리포트 요약들이다. 종합해라.\n\n" + "\n".join(lines)
    try:
        return client.chat(model, _ENTITY_SYSTEM, prompt, temperature=0.4)
    except Exception as e:
        logger.warning("entity synthesis failed: %s", e)
        return reports[0].summary  # 폴백: 첫 요약


def synthesize_digest(client: OllamaClient, model: str, reports: list[Report]) -> DigestResult:
    """장문 종합 + 인용도 상위 5개 소스 선정. 소스 파싱 실패 시 조회수 상위로 폴백."""
    labeled = [f"[S{i + 1}] {r.label}\n  {r.summary}" for i, r in enumerate(reports)]
    prompt = (
        f"아래는 관련 리포트 {len(reports)}건의 요약이다. 소스 id 를 붙였다.\n\n"
        + "\n".join(labeled)
    )
    raw = client.chat(model, _DIGEST_SYSTEM, prompt, temperature=0.5)

    # SOURCES 줄 파싱 → 본문에서 제거
    sources: list[Report] = []
    match = _SOURCES_RE.search(raw)
    if match:
        for token in re.findall(r"S(\d+)", match.group(1)):
            idx = int(token) - 1
            if 0 <= idx < len(reports) and reports[idx] not in sources:
                sources.append(reports[idx])
        raw = _SOURCES_RE.sub("", raw).strip()

    # 폴백·보충: 5개 미만이면 조회수 상위(select_top)로 채운다(중복 제거).
    if len(sources) < 5:
        for r in select_top(reports, top_n=len(reports)):
            if r not in sources:
                sources.append(r)
            if len(sources) >= 5:
                break

    return DigestResult(
        text=raw,
        category=reports[0].category if reports else "",
        report_count=len(reports),
        sources=sources[:5],
    )


_NEWS_SYSTEM = (
    "너는 시장 뉴스를 교차 분석하는 애널리스트다. 여러 기사 제목과 본문 발췌를 종합해 "
    "'지금 시장에서 무슨 일이 왜 일어나고 있는지'를 3~5줄로 서술한다. "
    "단순 제목 나열 금지, 인과·맥락 중심. 수치가 있으면 포함. 모르면 아는 척하지 않는다. "
    "종합 본문만 출력한다."
)


def synthesize_news(client: OllamaClient, model: str, blocks: list[str]) -> str:
    """기사 제목·본문 발췌 블록들을 종합해 서술형 시장 요약을 만든다. 실패 시 빈 문자열."""
    if not blocks:
        return ""
    prompt = "다음은 오늘 시장 뉴스들이다. 종합해 서술해라.\n\n" + "\n\n".join(blocks)
    try:
        return client.chat(model, _NEWS_SYSTEM, prompt, temperature=0.4)
    except Exception as e:
        logger.warning("news synthesis failed: %s", e)
        return ""


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
