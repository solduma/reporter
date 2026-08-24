"""외부 HTTP 공통 세션 — timeout 기본값 강제 + 전송 오류 지수 백오프 재시도.

external 어댑터들이 제각각 방어하던 것을 한 곳으로 모은다. HTTP 4xx/5xx 는
raise_for_status 하지 않고 그대로 반환한다(호출측이 응답 본문으로 판단 — graceful
degrade 계약 유지). 재시도 대상은 연결·타임아웃 같은 전송 계층 오류뿐이다.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15
_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.0


class ResilientSession(requests.Session):
    """timeout 기본 강제 + 전송 오류 지수 백오프 재시도 세션(get/post 전체 적용)."""

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
        last: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                return super().request(method, url, **kwargs)
            except requests.RequestException as e:
                last = e
                if attempt < _RETRY_ATTEMPTS:
                    wait = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    log.warning(
                        "external %s %s 실패(시도 %d/%d): %s — %.0fs 후 재시도",
                        method,
                        url[:70],
                        attempt,
                        _RETRY_ATTEMPTS,
                        e,
                        wait,
                    )
                    time.sleep(wait)
        assert last is not None
        raise last


def resilient_session() -> ResilientSession:
    return ResilientSession()


def resilient_get(url: str, **kwargs) -> requests.Response:
    """세션 생성 없이 단발 GET(모듈 레벨 호출용)."""
    return resilient_session().request("GET", url, **kwargs)
