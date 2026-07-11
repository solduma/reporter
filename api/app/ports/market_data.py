"""MarketDataPort — 봉(시세) 외부 조회 인터페이스.

candle_service 는 시장(KR/US)별 소스(네이버·KIS·네이버-foreign)를 직접 분기하는 대신
이 포트에 의존한다. 구현(adapters/market)이 소스를 감춘다. US 확장은 이 포트에 새 어댑터를
꽂는 문제로 축소된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # 런타임 결합 없음 — 봉 타입 힌트로만 참조.
    from app.domain.candle import Candle


class MarketDataPort(Protocol):
    """한 시장의 봉 조회. 구현체가 소스(네이버/KIS/…)를 캡슐화한다."""

    def fetch_periodic(
        self, code: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """[start, end] 구간의 일/주/월봉. 실패·데이터 없으면 빈 리스트."""
        ...

    def fetch_intraday_30min(self, code: str) -> list[Candle]:
        """가용 30분봉(최근 창). 미지원 시장이면 빈 리스트."""
        ...
