"""실시간 시세 어댑터 — KIS WebSocket 체결가 매니저(driven adapter).

기존 `services.realtime` 을 이 패키지(kis_ws)로 옮겼다. 공개 진입점은 프로세스 싱글턴 manager.
"""

from app.adapters.realtime.kis_ws import RealtimeManager, manager

__all__ = ["RealtimeManager", "manager"]
