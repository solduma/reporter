"""캘린더 이벤트 LLM 텍스트 — 과거는 '지수 영향·이유', 미래는 '시장 기대치'.

analysis_comment 의 해시캐싱 패턴: 이벤트의 LLM 입력(제목·날짜·수치·과거/미래 여부)을 해시해
inputs_hash 와 비교, 바뀐(또는 텍스트 없는) 이벤트에만 LLM 을 호출한다. 매 배치 전건 재생성
방지(Ollama ~수십초/건). LLM 미설정(get_llm None) 또는 실패 시 조용히 건너뛴다(graceful degrade).
consensus 무료 공식 소스가 없어, 미래 기대치는 LLM 의 일반 지식 기반 추정임을 프롬프트에 명시한다.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import get_llm
from app.config import Settings, get_settings
from app.db.models import CalendarEvent
from app.ports.llm import LLMError, LLMPort
from app.services.sentiment import (
    _extract_json,  # LLM 응답에서 JSON 관대 추출(코드펜스·잡텍스트 허용)
)

logger = logging.getLogger(__name__)

_PAST_SYSTEM = (
    "너는 매크로·시장 이벤트가 지수에 미친 영향을 사후 분석하는 애널리스트다. 지난 이벤트의 실제치와 "
    "직전치를 근거로, 그 결과가 시장(특히 지수)에 어떻게·왜 작용했는지 설명하고, 지수에 미친 방향을 "
    "판정한다. 지수에 긍정적이었으면 positive, 부정적이었으면 negative, 뚜렷하지 않거나 판단 어려우면 "
    "neutral. 과장·단정을 피하고 모르면 아는 척하지 않는다. 반드시 아래 JSON 형식만 출력한다.\n"
    '{"impact": "지수 영향·이유를 2~3문장으로", "direction": "positive|negative|neutral"}'
)
_FUTURE_SYSTEM = (
    "너는 다가올 매크로·시장 이벤트에 대한 시장의 일반적 기대와 관전 포인트를 정리하는 애널리스트다. "
    "정확한 컨센서스 수치는 제공되지 않으니 구체 숫자를 지어내지 말고, '무엇을 주목하는지·어느 방향이면 "
    "지수에 어떤 의미인지'를 2~3문장으로 설명한다. 확정 예측이 아니라 관전 포인트임을 분명히 한다."
)

_MODEL_TAG = "calendar-v3"  # 개정 시 올려 재생성 유도(v3: 과거 JSON {impact,direction} 방향 분류)


def _inputs_hash(ev: CalendarEvent, is_past: bool) -> str:
    payload = "|".join([
        _MODEL_TAG, ev.title, ev.event_date.isoformat(), ev.region, ev.kind,
        str(ev.actual), str(ev.previous), str(ev.consensus), "past" if is_past else "future",
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _prompt(ev: CalendarEvent, is_past: bool, today: date) -> str:
    # 오늘 날짜와 과거/미래 여부를 명시(LLM 이 날짜를 오판해 '미래라 분석 불가'로 답하지 않게).
    when = "이미 지난 이벤트" if is_past else "아직 도래하지 않은 이벤트"
    lines = [
        f"오늘 날짜: {today.isoformat()}",
        f"이벤트: {ev.title}",
        f"일자: {ev.event_date.isoformat()} ({when})",
        f"지역: {ev.region}",
    ]
    if ev.actual:
        lines.append(f"실제치: {ev.actual}")
    if ev.previous:
        lines.append(f"직전치: {ev.previous}")
    if ev.consensus:
        lines.append(f"시장예상: {ev.consensus}")
    if is_past:
        note = "" if ev.actual else "\n(구체 실적 수치는 제공되지 않았으니, 일반적으로 알려진 결과·맥락 범위에서 설명하라.)"
        tail = "위 이벤트의 결과가 지수에 미친 영향과 그 이유를 설명해라." + note
    else:
        tail = "위 이벤트에 대해 시장이 무엇을 기대·주목하는지 관전 포인트를 정리해라."
    return "\n".join(lines) + "\n\n" + tail


_DIRECTIONS = {"positive", "negative", "neutral"}


def _generate_one(
    client: LLMPort, model: str, ev: CalendarEvent, is_past: bool, today: date
) -> tuple[str, str | None] | None:
    """LLM 텍스트 생성. 반환 (text, direction). 미래 이벤트는 direction=None.

    과거 이벤트는 JSON {impact, direction} 으로 받아 방향까지 분류(프론트 색칠용).
    """
    system = _PAST_SYSTEM if is_past else _FUTURE_SYSTEM
    try:
        raw = client.chat(model, system, _prompt(ev, is_past, today), temperature=0.3).strip()
    except LLMError as e:
        logger.warning("calendar LLM failed for %s: %s", ev.title, e)
        return None
    if not is_past:
        return (raw, None) if raw else None
    # 과거: JSON {impact, direction}. 파싱 실패 시 원문을 text 로, 방향은 neutral 로 폴백.
    data = _extract_json(raw)
    if data and data.get("impact"):
        direction = str(data.get("direction", "")).lower()
        if direction not in _DIRECTIONS:
            direction = "neutral"
        return str(data["impact"]).strip(), direction
    return (raw, "neutral") if raw else None


def generate_pending(
    db: Session,
    settings: Settings | None = None,
    today: date | None = None,
    limit: int = 40,
) -> int:
    """텍스트가 없거나 입력이 바뀐 이벤트에만 LLM 텍스트를 채운다. 생성 건수 반환.

    limit 로 배치당 LLM 호출을 제한(수십초/건). LLM 미설정 시 0.
    """
    settings = settings or get_settings()
    today = today or date.today()
    client = get_llm(settings)
    if client is None:
        return 0

    events = db.execute(select(CalendarEvent).order_by(CalendarEvent.event_date)).scalars().all()
    generated = 0
    for ev in events:
        if generated >= limit:
            break
        is_past = ev.event_date <= today
        h = _inputs_hash(ev, is_past)
        existing = ev.impact_text if is_past else ev.expectation_text
        if existing and ev.inputs_hash == h:
            continue  # 최신 — 재생성 불필요
        result = _generate_one(client, settings.insight_model, ev, is_past, today)
        if result is None:
            continue
        text, direction = result
        if is_past:
            ev.impact_text = text
            ev.impact_direction = direction
        else:
            ev.expectation_text = text
        ev.inputs_hash = h
        generated += 1
    db.commit()
    logger.info("calendar LLM: generated %d texts", generated)
    return generated
