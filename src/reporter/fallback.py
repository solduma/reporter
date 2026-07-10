"""폴백(1차 소스/방법 실패 → 2차 대안 전환) 발생을 일관되게 기록하는 공용 지점.

프로젝트 전반의 폴백은 지금까지 제각각의 logger.warning 으로만 남아 grep·집계가 어려웠다.
이 모듈로 (1) 공통 마커 프리픽스 "FALLBACK[key]" 로 로그를 통일하고, (2) sink 를 통해
선택적으로 영속화한다.

계층 주의: 이 모듈은 reporter(하위 계층)에 있어 CLI·API 양쪽에서 import 된다. DB 영속화는
API 만 담당(단일 writer 불변식)하므로, API 가 startup 에서 DB sink 를 register_sink 로 등록한다.
sink 미등록 환경(CLI)에서는 로그만 남는다. sink 실패는 절대 폴백 경로를 깨지 않는다(전부 흡수).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# sink 시그니처: (key, reason, detail, context) -> None
FallbackSink = Callable[[str, str, str | None, dict], None]

_sinks: list[FallbackSink] = []


def register_sink(sink: FallbackSink) -> None:
    """폴백 이벤트를 받을 sink 를 등록한다(예: DB 영속화). 중복 등록은 무시한다."""
    if sink not in _sinks:
        _sinks.append(sink)


def clear_sinks() -> None:
    """등록된 sink 를 모두 제거한다(테스트 격리용)."""
    _sinks.clear()


def log_fallback(key: str, *, reason: str, detail: str | None = None, **context: object) -> None:
    """폴백 발생을 기록한다.

    key: 계층 식별자. 예 "chart.naver_to_kis", "market_brief.closing_to_all".
    reason: 무엇이 실패해 폴백했는지 사람이 읽는 요약.
    detail: 대상 식별자(종목코드·URL 등) 옵션.
    context: 추가 구조화 맥락(sink 가 JSON 으로 보존).
    """
    suffix = f" | {detail}" if detail else ""
    logger.warning("FALLBACK[%s] %s%s", key, reason, suffix)
    for sink in _sinks:
        try:
            sink(key, reason, detail, dict(context))
        except Exception as e:  # sink 실패가 폴백 경로(본 기능)를 깨면 안 된다
            logger.warning("fallback sink failed for %s: %s", key, e)
