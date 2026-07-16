"""DART OpenAPI 요청 스로틀 + 키 폴오버 — 모든 DART HTTP 호출이 이 게이트를 통과한다.

DART 는 키당 일일 한도(2만)와 별개로, 짧은 시간에 연속 요청하면 IP 를 TCP 레벨로 차단한다
(status 020 조차 못 받고 연결이 끊김). 백필이 종목당 수십 콜을 간격 없이 몰아 보내다 IP 밴을
유발한 사례가 있어, 프로세스 전역으로 최소 요청 간격을 강제해 예방한다.

또한 키 링(primary→backup)을 소유해, 응답 본문이 status 020(한도초과)이면 다음 키로 폴오버한다.
document.xml(바이너리 엔드포인트)조차 020 시 zip 이 아닌 `<status>020</status>` XML 을 주므로,
JSON·XML·바이너리를 한 지점(응답 본문)에서 감지해야 견고하다. 모든 키가 소진되면 020 이 그대로
전파돼 호출측(client)이 DartQuotaExceeded 로 올린다.

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

# 키 링 — 활성 인덱스는 020 폴오버로만 전진한다(성공 시 유지해 재낭비 방지).
_keyring: list[str] = []
_active_idx = 0
_keyring_lock = threading.Lock()

# 응답 본문에서 020 을 감지하는 최소 시그니처(JSON/XML 공통). 149바이트 XML 도, JSON 도 매칭.
_QUOTA_SIG = b'"status":"020"'
_QUOTA_SIG_XML = b"<status>020</status>"


def configure_keys(*keys: str) -> None:
    """DART 키 링을 설정한다(primary, backup...). 빈 키는 무시. 활성 인덱스를 primary 로 리셋.

    배치·딥다이브 진입 시 호출하면 매 실행마다 primary 부터 재시도한다(자정 한도 회복 반영)."""
    global _keyring, _active_idx
    with _keyring_lock:
        _keyring = [k for k in keys if k]
        _active_idx = 0


def active_key() -> str | None:
    """현재 활성 DART 키. 링이 비었으면 None(호출측이 키 미설정 처리)."""
    with _keyring_lock:
        return _keyring[_active_idx] if _active_idx < len(_keyring) else None


def _rotate_key(exhausted: str) -> bool:
    """020 을 준 키(exhausted)가 아직 활성이면 다음 키로 전진. 다음 키가 있으면 True.

    동시 요청이 같은 020 을 여럿 봐도 인덱스는 한 번만 전진하도록 현재 활성 키와 대조한다.
    """
    global _active_idx
    with _keyring_lock:
        if _active_idx < len(_keyring) and _keyring[_active_idx] == exhausted:
            _active_idx += 1
        return _active_idx < len(_keyring)


def _is_quota_body(content: bytes) -> bool:
    head = content[:512]
    return _QUOTA_SIG in head.replace(b" ", b"") or _QUOTA_SIG_XML in head


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
    """스로틀 + 키 폴오버를 적용한 DART GET.

    params 에 crtfc_key 가 있으면 활성 키로 덮어쓰고, 응답이 020(한도초과)이면 다음 키로
    회전해 재시도한다. 링이 없거나(키 직접 지정) 모든 키 소진 시엔 마지막 응답을 그대로 반환
    (client 가 status 020 을 보고 DartQuotaExceeded 를 올린다)."""
    params = kwargs.get("params")
    ring_has_keys = active_key() is not None
    uses_key = isinstance(params, dict) and "crtfc_key" in params

    # 링을 안 쓰는 호출(키 미설정·params 없음)은 단순 스로틀 GET.
    if not (ring_has_keys and uses_key):
        _wait_turn()
        return session.get(url, **kwargs)

    while True:
        key = active_key()
        params["crtfc_key"] = key
        _wait_turn()
        resp = session.get(url, **kwargs)
        if not _is_quota_body(resp.content):
            return resp
        # 020 — 다음 키로 회전. 남은 키가 없으면 이 응답(020)을 반환해 client 가 예외로 올린다.
        if not _rotate_key(key):
            return resp
