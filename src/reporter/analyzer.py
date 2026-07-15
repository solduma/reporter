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
각 섹션 사이는 빈 줄로 띄우고, 종목명·수치 등 핵심어만 굵게(**...**) 강조한다(남발 금지).

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
    "핵심 컨센서스와 이견을 함께 짚어 종합한다. 목표가·투자의견 수치가 있으면 포함. "
    "모르면 아는 척하지 않는다.\n"
    "내용이 전환되는 지점(예: 컨센서스 → 이견·리스크)에서 문단을 나누고 사이를 빈 줄로 띄운다. "
    "종목명·목표가 등 핵심 수치는 굵게(**...**). 결과 본문만 출력한다."
)

_DIGEST_SYSTEM = (
    "너는 특정 분야 증권사 리포트를 교차 분석하는 투자 자문위원이다. "
    "모든 리포트를 참고해 '지금 이 분야에서 뭐가 중요한지'를 종합한다. "
    "단순 나열이 아니라 재해석하고, 리스크도 솔직히 짚는다. 모르면 아는 척하지 않는다.\n"
    "내용 흐름이 바뀌는 지점에서 문단을 나누고 사이를 빈 줄로 띄워 읽기 쉽게 한다(문단 수는 내용에 맞게). "
    "정말 중요한 종목·수치만 굵게(**...**) 강조하고, 이모지는 어울리는 곳에만 자연스럽게 쓴다.\n"
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
    "'지금 시장에서 무슨 일이 왜 일어나고 있는지'를 전달한다. "
    "단순 제목 나열 금지, 인과·맥락 중심. 수치가 있으면 포함. 모르면 아는 척하지 않는다.\n"
    "내용 흐름이 바뀌는 지점에서 문단을 나누고 사이를 빈 줄로 띄워 읽기 쉽게 한다(문단 수는 내용에 맞게). "
    "핵심 수치나 종목은 굵게(**...**) 강조하고, 이모지는 어울리는 곳에만 자연스럽게 쓴다. 종합 본문만 출력한다."
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


_FORECAST_SYSTEM = (
    "너는 전날 국내 증시 마감과 간밤 미국 증시 마감을 함께 읽고 '오늘 국내 장'을 전망하는 "
    "투자 전략가다. 과거 리뷰를 요약하는 게 아니라, 그 흐름이 오늘 장에 어떻게 이어질지 "
    "예상한다. 미국 마감이 국내에 선행한다는 점을 활용하되, 근거 없는 단정은 피하고 "
    "리스크도 솔직히 짚는다. 모르면 아는 척하지 않는다."
)

_FORECAST_TEMPLATE = """아래는 전날 국내 마감시황과 간밤 미국 마감시황을 포함한 증권사 리포트 {count}건의 요약이다.

{summaries}

위 흐름(전날 국내 마감 + 간밤 미국 마감)을 종합해 '오늘 국내 장'을 예상하는 브리핑을 아래 형식으로 작성해라.
형식과 이모지를 그대로 지키고, 각 섹션 사이는 빈 줄로 띄운다. 종목명·수치 등 핵심어만 굵게(**...**) 강조(남발 금지).

🔮 오늘의 핵심 (3줄)
→ 전날·간밤 흐름이 오늘 장에 미칠 핵심 포인트

📊 주목 테마
→ 오늘 주목할 섹터/테마 2~3개, 예상 근거 포함

💎 주목 종목 (최대 5개)
→ 흐름·수급상 오늘 주목할 종목

⚠️ 리스크 요인
→ 오늘 장에서 경계할 리스크"""


def synthesize_forecast(client: OllamaClient, model: str, reports: list[Report]) -> Briefing:
    """전날 국내마감 + 간밤 미장마감을 종합해 '오늘 예상' 브리핑을 만든다."""
    lines = [f"- {r.label}\n  {r.summary}" for r in reports]
    prompt = _FORECAST_TEMPLATE.format(count=len(reports), summaries="\n".join(lines))
    text = client.chat(model, _FORECAST_SYSTEM, prompt, temperature=0.5)
    return Briefing(
        text=text,
        report_count=len(reports),
        categories=sorted({r.category for r in reports}),
    )


_INTRADAY_SYSTEM = (
    "너는 지금 열려 있는 국내 장을 실시간으로 읽는 투자 전략가다. 과거 리뷰나 예상이 아니라 "
    "'지금 이 순간 장이 어떻게, 왜 움직이고 있는지'를 전달한다. 제시된 실시간 지수·환율 수치와 "
    "장중 뉴스를 근거로 삼고, 근거 없는 단정은 피하며 리스크도 솔직히 짚는다. 모르면 아는 척하지 않는다. "
    "직전 시황이 함께 주어지면, 지금 상황이 그와 달라졌을 때 '장 초엔 이랬으나 지금은 이렇게 바뀌었다' "
    "식으로 변화를 반드시 짚는다. 직전 시황을 그대로 되풀이하지 말고 항상 가장 최신 상황을 우선한다."
)

_INTRADAY_TEMPLATE = """지금은 국내 장중이다. 아래는 실시간 지수·환율 스냅샷과 최신 장중 시장 뉴스다.
{prev}
[실시간 지수·환율]
{quotes}

[최신 장중 뉴스]
{news}

위 실시간 데이터를 근거로 '지금 장 상황'을 아래 형식으로 작성해라. 직전 시황이 있다면 그 대비 무엇이
달라졌는지(방향 전환·새 재료·기존 재료 소멸)를 우선 반영한다 — 바뀌었으면 '장 초엔 …였으나 지금은 …'
식으로 대조하고, 큰 변화가 없으면 흐름이 유지되고 있음을 밝힌다.
형식과 이모지를 그대로 지키고, 각 섹션 사이는 빈 줄로 띄운다. 지수·수치·종목명 등 핵심어만 굵게(**...**) 강조(남발 금지).

📊 지금 장 상황 (3줄)
→ 코스피/코스닥 현재 등락과 그 배경 (제시된 지수·환율 수치를 인용, 직전 대비 변화가 있으면 함께)

🔥 움직이는 테마·뉴스
→ 최신 장중 뉴스가 가리키는 주도 흐름 2~3개

⚠️ 지금 체크포인트
→ 오후장·환율·외국인 수급 관점에서 지금 경계할 요인"""


def synthesize_intraday(
    client: OllamaClient,
    model: str,
    quote_lines: list[str],
    news_blocks: list[str],
    prev_summary: str | None = None,
) -> Briefing:
    """실시간 지수·환율 스냅샷 + 장중 뉴스로 '지금 장 상황' 브리핑을 만든다(장중용).

    리서치 요약이 아니라 실시간 수치 기반이라 temperature 를 낮춰 정확성을 우선한다.
    prev_summary(직전 시황)가 있으면 '장 초엔 이랬으나 지금은' 식 대조를 하게 한다.
    """
    quotes = "\n".join(quote_lines) if quote_lines else "(지수·환율 조회 실패)"
    news = "\n\n".join(news_blocks) if news_blocks else "(장중 뉴스 없음)"
    prev_block = (
        f"\n[직전 시황(같은 날 앞서 작성)]\n{prev_summary}\n"
        if prev_summary and prev_summary.strip()
        else ""
    )
    prompt = _INTRADAY_TEMPLATE.format(quotes=quotes, news=news, prev=prev_block)
    text = client.chat(model, _INTRADAY_SYSTEM, prompt, temperature=0.3)
    return Briefing(text=text, report_count=len(news_blocks), categories=["intraday"])


_REVIEW_SYSTEM = (
    "너는 오늘 마감한 국내 증시를 리뷰하고 '내일 국내 장'을 전망하는 투자 전략가다. "
    "오늘 무슨 일이 왜 일어났는지 정리하고, 그 흐름과 오늘 밤 미국 장 관전 포인트를 "
    "엮어 내일을 전망한다. 근거 없는 단정은 피하고 리스크도 솔직히 짚는다. 모르면 아는 척하지 않는다."
)

_REVIEW_TEMPLATE = """아래는 오늘 국내 증시 마감시황과 관련 증권사 리포트 {count}건의 요약이다.

{summaries}

위 내용을 바탕으로 '오늘 마감 리뷰 + 내일 전망' 브리핑을 아래 형식으로 작성해라.
형식과 이모지를 그대로 지키고, 각 섹션 사이는 빈 줄로 띄운다. 종목명·수치 등 핵심어만 굵게(**...**) 강조(남발 금지).

📉 오늘 마감 요약 (3줄)
→ 오늘 국내 장이 어떻게 움직였고 왜 그랬는지

🌙 오늘 밤 관전 포인트
→ 내일에 영향 줄 미국 장·이벤트 관점 2~3개

🔮 내일 전망
→ 오늘 흐름과 밤사이 변수로 본 내일 국내 장 시나리오

⚠️ 리스크 요인
→ 내일 장에서 경계할 리스크"""


def synthesize_closing_review(client: OllamaClient, model: str, reports: list[Report]) -> Briefing:
    """오늘 국내 마감 리뷰 + 내일 전망 브리핑을 만든다(장 마감 후용)."""
    lines = [f"- {r.label}\n  {r.summary}" for r in reports]
    prompt = _REVIEW_TEMPLATE.format(count=len(reports), summaries="\n".join(lines))
    text = client.chat(model, _REVIEW_SYSTEM, prompt, temperature=0.5)
    return Briefing(
        text=text,
        report_count=len(reports),
        categories=sorted({r.category for r in reports}),
    )
