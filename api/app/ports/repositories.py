"""Repository 포트 — 영속화 인터페이스(Protocol).

응용 계층(services)은 SQLAlchemy Session 대신 이 인터페이스에 의존한다. 구현은
adapters/persistence 가 제공한다. 시그니처는 데이터 타입(현재는 anemic ORM 엔티티)을
주고받되, 세션·쿼리 같은 영속화 '메커니즘'은 노출하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # 런타임 결합 없음 — 타입 힌트로만 참조(도메인 엔티티 대체 여지).
    from app.db.models import Holding, PriceCandle, PriceCandleIntraday, Timeframe


class HoldingInput(Protocol):
    """보유종목 upsert 입력의 구조(서비스 타입에 결합하지 않도록 구조적 타입)."""

    stock_code: str
    shares: float
    avg_cost: float
    stop_loss: float | None
    note: str | None


class CandleInput(Protocol):
    """외부에서 조회한 봉 1건의 구조(어댑터 upsert 입력). 서비스 타입에 결합하지 않도록 구조적 타입."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class UniverseRepository(Protocol):
    """유니버스 스냅샷 조회."""

    def latest_snapshot_date(self) -> date | None:
        """가장 최근 유니버스 스냅샷 날짜. 없으면 None."""
        ...


class CandleRepository(Protocol):
    """봉(일/주/월 + 30분) 영속화."""

    def read_periodic(self, code: str, tf: str) -> list[PriceCandle]:
        """저장된 일/주/월봉을 날짜 오름차순으로 반환."""
        ...

    def read_intraday(self, code: str, days: int = 14) -> list[PriceCandleIntraday]:
        """저장된 30분봉 최근 days 일치를 시각 오름차순으로 반환."""
        ...

    def latest_bar_date(self, code: str, tf: Timeframe) -> date | None:
        """저장된 해당 tf 봉의 최신 날짜. 없으면 None."""
        ...

    def upsert_periodic(self, code: str, tf: Timeframe, candles: list[CandleInput]) -> int:
        """봉들을 upsert 하고 반영 건수를 반환."""
        ...


class HoldingRepository(Protocol):
    """개인 보유종목 영속화(단일 사용자)."""

    def list_all(self) -> list[Holding]:
        """보유종목 전체(종목코드 오름차순)."""
        ...

    def get(self, stock_code: str) -> Holding | None:
        """한 종목의 보유 정보. 없으면 None."""
        ...

    def upsert(self, item: HoldingInput) -> Holding:
        """보유종목 저장(종목당 1행, 있으면 갱신). 저장된 행 반환."""
        ...

    def delete(self, stock_code: str) -> bool:
        """보유종목 삭제. 삭제된 행이 있으면 True."""
        ...
