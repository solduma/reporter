"""리포트 본문에서 BUY/SELL/HOLD 센티먼트 + 근거 + 한 줄 요약을 GLM 으로 분류한다.

기존 analyzer._SUMMARY_SYSTEM 은 자유 텍스트 요약만 하므로, 구조화 출력(JSON)을
강제하는 별도 프롬프트를 둔다. 형식 이탈 시 HOLD 로 폴백한다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from reporter.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

_SYSTEM = (
    "너는 증권사 리포트를 분석해 투자 신호를 추출하는 애널리스트다. "
    "리포트가 해당 종목/산업 주가에 긍정적이면 BUY, 부정적이면 SELL, 중립·판단불가면 HOLD 로 분류한다. "
    "반드시 아래 JSON 형식만 출력한다. 다른 말은 절대 붙이지 않는다.\n"
    '{"sentiment": "BUY|SELL|HOLD", "one_liner": "리포트 핵심을 담은 한 줄(120자 이내)", '
    '"rationale": "왜 그 신호인지 근거(150자 이내, 구체 수치·목표가 있으면 포함)"}'
)

_VALID = {"BUY", "SELL", "HOLD"}


@dataclass
class SentimentResult:
    sentiment: str  # BUY | SELL | HOLD
    one_liner: str
    rationale: str


def _extract_json(raw: str) -> dict | None:
    """LLM 응답에서 첫 JSON 오브젝트를 관대하게 추출한다 (코드펜스·잡텍스트 허용)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def classify(client: OllamaClient, model: str, category: str, title: str, text: str) -> SentimentResult:
    """리포트 본문(앞 5페이지 권장)으로 센티먼트를 분류한다. 실패 시 HOLD 폴백."""
    prompt = f"[{category}] {title}\n\n{text[:6000]}"
    try:
        raw = client.chat(model, _SYSTEM, prompt, temperature=0.2)
    except OllamaError as e:
        logger.warning("sentiment GLM failed for %s: %s", title, e)
        return SentimentResult("HOLD", "", "")

    data = _extract_json(raw)
    if not data:
        logger.warning("sentiment JSON parse failed for %s: %r", title, raw[:200])
        return SentimentResult("HOLD", raw[:120].strip(), "")

    sentiment = str(data.get("sentiment", "")).upper().strip()
    if sentiment not in _VALID:
        sentiment = "HOLD"
    return SentimentResult(
        sentiment=sentiment,
        one_liner=str(data.get("one_liner", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
    )
