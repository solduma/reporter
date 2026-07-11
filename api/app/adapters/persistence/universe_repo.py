"""UniverseRepository 의 SQLAlchemy 구현."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import UniverseSnapshot


class SqlUniverseRepository:
    """유니버스 스냅샷 조회(SQLAlchemy). ports.UniverseRepository 를 만족한다."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_snapshot_date(self) -> date | None:
        return self._db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
