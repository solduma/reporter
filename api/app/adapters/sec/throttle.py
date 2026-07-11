"""SEC EDGAR 요청 스로틀 — 모든 SEC HTTP 호출이 이 게이트를 통과한다.

SEC 는 초당 10요청을 초과하면 IP 를 차단한다(공식 정책). 프로세스 전역으로 최소 간격을
강제해 예방한다(dart_throttle 과 동일 패턴). User-Agent 는 호출측이 헤더로 붙인다.
"""

from __future__ import annotations

import threading
import time

import requests

# SEC 요청 간 최소 간격(초). 공식 한도 10req/s 보다 보수적으로(초당 ~8건).
_MIN_INTERVAL_S = 0.12

_lock = threading.Lock()
_last_request_at = 0.0


def _wait_turn() -> None:
    global _last_request_at
    with _lock:
        now = time.monotonic()
        gap = now - _last_request_at
        if gap < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - gap)
        _last_request_at = time.monotonic()


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """스로틀을 적용한 SEC GET. session.get 과 동일 시그니처."""
    _wait_turn()
    return session.get(url, **kwargs)
