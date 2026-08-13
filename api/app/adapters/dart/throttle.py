"""DART OpenAPI 요청 스로틀 + 키별 budget + 키 폴오버 — 모든 DART HTTP 호출이 이 게이트를 통과한다.

DART 는 키당 일일 한도(2만 020)와 별개로 짧은 시간에 연속 요청하면 IP 를 TCP 레벨로 차단한다
(020 도 못 받고 연결이 끊김). 이를 방지하기 위해:
  1. 키별 rate limiter — 키마다 초당 ~3건(0.34s 간격) 이상 못 넘기도록.
  2. 키별 daily budget — 키당 90%(18,000회) 에 도달하면 그 키를 잠그고 다음 키로 폴오버.
  3. 모든 키가 잠기면 020 응답을 그대로 반환 (호출측이 DartQuotaExceeded 처리).

전역 락은 키별 간격 check-and-sleep 만mutex로 보호하고, 키 자체는 독립적으로 운영된다.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import requests

# ─── rate limit ────────────────────────────────────────────────────────────────
# IP 밴 방지를 위한 키별 최소 간격. 초당 ~3건 (0.34s 간격).
_MIN_INTERVAL_S = 0.34

# 키별 rate limiter 상태 — 키 해시 → (마지막 요청 시각, threading.Lock).
# 새 키는 처음 호출 시 동적 등록 (키 문자열은 길고 고정이라 dict 키로 안전).
_rate_limiter: dict[str, tuple[float, threading.Lock]] = {}
_rate_limiter_lock = threading.Lock()


def _rate_wait(key: str) -> None:
    """키별 최소 간격이 지나도록 대기. 키별 별도 lock이라 여러 프로세서가
    서로 다른 키를 쓸 때 서로를 차단하지 않는다."""
    now = time.monotonic()
    with _rate_limiter_lock:
        last_at, key_lock = _rate_limiter.get(key, (0.0, threading.Lock()))
    with key_lock:
        gap = now - last_at
        if gap < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - gap)
    with _rate_limiter_lock:
        _rate_limiter[key] = (time.monotonic(), key_lock)


# ─── daily budget per key ────────────────────────────────────────────────────
# 키별 일일 budget — 키당 2만 중 90% = 18,000회. 예산 소진 시 해당 키를 잠근다.
_KEY_BUDGET = 18_000

# 키별 budget 상태 — 키 해시 → (오늘 날짜 str, 남은 횟수 int).
# 카운터는 프로세스 재시작 시 리셋되지만, budget은 매일 자정(UTC)에 만료된다.
_budget: dict[str, tuple[str, int]] = {}
_budget_lock = threading.Lock()


def _budget_key(key: str) -> str:
    """budget dict의 entry를 키로 리턴. 새 키는 _budget_check에서 등록."""
    return key


def _budget_check(key: str) -> bool:
    """키의 budget 잔량을 검사. 0 이하면 False (호출 제한). budget 매일 자정 만료.

    True 반환 시 이번 budget 소진 전까지 같은 키로 계속 호출 가능."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with _budget_lock:
        date_str, remaining = _budget.get(key, (today, _KEY_BUDGET))
        if date_str != today:  # 날짜 바뀌면 budget 리셋.
            _budget[key] = (today, _KEY_BUDGET)
            return True
        if remaining <= 0:
            return False
        _budget[key] = (date_str, remaining - 1)
        return True


def _is_exhausted(key: str) -> bool:
    """키 budget이 오늘 소진됐으면 True."""
    return not _budget_check(key)


# ─── 키 링 ───────────────────────────────────────────────────────────────────
# primary → backup 순서의 키 리스트. 020 또는 budget 소진 시 다음 키로 회전.
_keyring: list[str] = []
_active_idx = 0
_ring_lock = threading.Lock()


def configure_keys(*keys: str) -> None:
    """키 링을 설정한다(primary, backup...). 배치 진입 시 호출하면 primary부터 재시도.

    키 budget은 매일 자정(UTC)에 만료되므로 configure_keys는 키budget을 리셋하지 않는다."""
    global _keyring, _active_idx
    with _ring_lock:
        _keyring = [k for k in keys if k]
        _active_idx = 0


def active_key() -> str | None:
    """현재 활성 키. budget 소진 키는 건너뛴다."""
    global _active_idx
    with _ring_lock:
        while _active_idx < len(_keyring):
            key = _keyring[_active_idx]
            if not _is_exhausted(key):
                return key
            _active_idx += 1
        return None


def _rotate_on_quota(key: str) -> bool:
    """020(budget 소진 포함)을 받은 키를 활성에서 잠금 처리하고 다음 키로 전진.

    호출 측에서 budget 소진 키가 계속 잡히지 않도록 active_idx를 여기서만 전진한다."""
    global _active_idx
    with _ring_lock:
        if _active_idx < len(_keyring) and _keyring[_active_idx] == key:
            _active_idx += 1
        return _active_idx < len(_keyring)


def backfill_budget_exhausted() -> bool:
    """배치 백필 중단 판정. 모든 키 budget이 소진됐으면 True."""
    return active_key() is None


def remaining_budget() -> int:
    """오늘 남은 DART 예산 합계(전 키). 배치 백필의 동적 per_run 계산용.

    _budget_check 와 달리 잔량을 소비하지 않는다. 키링이 비어 있으면 0.
    budget 은 매일 자정(UTC)에 리셋되므로, 어제 등록된 잔량은 오늘 몫으로 재계산한다.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with _budget_lock:
        total = 0
        for key in _keyring:
            date_str, remaining = _budget.get(key, (today, _KEY_BUDGET))
            if date_str != today:
                remaining = _KEY_BUDGET
            total += remaining
        return total


# ─── 020 detection ────────────────────────────────────────────────────────────
_QUOTA_SIG = b'"status":"020"'
_QUOTA_SIG_XML = b"<status>020</status>"


def _is_quota_body(content: bytes) -> bool:
    head = content[:512]
    return _QUOTA_SIG in head.replace(b" ", b"") or _QUOTA_SIG_XML in head


# ─── main gate ──────────────────────────────────────────────────────────────
def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """DART GET 게이트 — rate limit + budget check + 020 폴오버.

    - rate limit: 키별 0.34s 간격 유지 (IP 밴 방지).
    - budget check: 키별 18,000회 소진 시 다음 키로 자동 폴오버.
    - 020 response: 키를 잠그고 다음 키로 회전. 모든 키 소진 시 020 응답 그대로 반환.
    """
    params = kwargs.get("params")
    ring_has_keys = active_key() is not None
    uses_key = isinstance(params, dict) and "crtfc_key" in params

    # 키 링을 쓰지 않는 호출은 rate limit만.
    if not (ring_has_keys and uses_key):
        # 호출자가 명시적 crtfc_key를 줄 때만 rate limit 적용 (그 외: 단순 HTTP).
        key = params.get("crtfc_key") if isinstance(params, dict) else None
        if key:
            _rate_wait(key)
        return session.get(url, **kwargs)

    while True:
        key = active_key()
        if key is None:  # 모든 키 budget 소진.
            return session.get(url, **kwargs)  # 020 응답 반환.

        params["crtfc_key"] = key
        _rate_wait(key)

        resp = session.get(url, **kwargs)

        if not _is_quota_body(resp.content):
            return resp

        # 020 응답 — 해당 키를 budget 소진 처리 후 다음 키로.
        _budget_lock.acquire()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        _budget[key] = (today, 0)
        _budget_lock.release()

        if not _rotate_on_quota(key):
            return resp  # 마지막 키까지 소진 — 020 그대로 반환.
