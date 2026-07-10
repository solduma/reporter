"""DB 세션·엔진. 1단계는 alembic 대신 create_all 로 스키마를 생성한다."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()
engine = create_engine(_settings.postgres_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# create_all 은 신규 테이블만 만들고 기존 테이블에 컬럼을 추가하지 않는다. alembic 도입 전까지
# 멱등 ADD COLUMN IF NOT EXISTS 로 기존 배포 스키마를 따라잡는다(Postgres 전용 구문).
_COLUMN_MIGRATIONS = (
    "ALTER TABLE daily_market_info ADD COLUMN IF NOT EXISTS phase VARCHAR(16) DEFAULT ''",
    "ALTER TABLE daily_market_info ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()",
    # EV/EBITDA·PSR 원자료 + 산출값(#135).
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS ebitda DOUBLE PRECISION",
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS net_debt DOUBLE PRECISION",
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS ev_ebitda DOUBLE PRECISION",
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS psr DOUBLE PRECISION",
    "ALTER TABLE peers ADD COLUMN IF NOT EXISTS ev_ebitda VARCHAR(32)",
    "ALTER TABLE peers ADD COLUMN IF NOT EXISTS psr VARCHAR(32)",
)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _COLUMN_MIGRATIONS:
            conn.execute(text(stmt))


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
