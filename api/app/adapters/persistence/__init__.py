"""영속화 어댑터 — SQLAlchemy 로 Repository 포트를 구현한다.

Session 을 주입받아 포트 인터페이스를 만족시킨다. 응용 계층은 포트 타입만 알고 이 구현은 모른다.
"""

from app.adapters.persistence.candle_repo import SqlCandleRepository
from app.adapters.persistence.universe_repo import SqlUniverseRepository

__all__ = ["SqlCandleRepository", "SqlUniverseRepository"]
