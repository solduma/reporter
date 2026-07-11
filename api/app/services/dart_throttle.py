"""DART OpenAPI 요청 스로틀 — 모든 DART HTTP 호출이 이 게이트를 통과한다.

DART 는 키당 일일 한도(2만)와 별개로, 짧은 시간에 연속 요청하면 IP 를 TCP 레벨로 차단한다
(status 020 조차 못 받고 연결이 끊김). 백필이 종목당 수십 콜을 간격 없이 몰아 보내다 IP 밴을
유발한 사례가 있어, 프로세스 전역으로 최소 요청 간격을 강제해 예방한다.

전역 락 + 마지막 요청 시각으로 스레드 안전하게 간격을 보장한다(백필·온디맨드 동시 실행 대비).
"""

from __future__ import annotations

import threading
import time

import requests

# DART 요청 간 최소 간격(초). 보수적으로 잡아 IP 밴을 피한다(초당 ~3건 이하).
_MIN_INTERVAL_S = 0.34

_lock = threading.Lock()
_last_request_at = 0.0


def _wait_turn() -> None:
    """마지막 DART 요청 이후 _MIN_INTERVAL_S 가 지나도록 대기한다(전역 직렬화)."""
    global _last_request_at
    with _lock:
        now = time.monotonic()
        gap = now - _last_request_at
        if gap < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - gap)
        _last_request_at = time.monotonic()


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """스로틀을 적용한 DART GET. session.get 과 동일 시그니처."""
    _wait_turn()
    return session.get(url, **kwargs)
