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
    # 미국 심볼(QQQ.O·XLK 등) 저장 위해 봉 stock_code 폭 확장(기존 6→16). 축소가 아니라 안전.
    "ALTER TABLE price_candles ALTER COLUMN stock_code TYPE VARCHAR(16)",
    # 배당(주당배당금·시가배당률) 컬럼(#172).
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS dps DOUBLE PRECISION",
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS div_yield DOUBLE PRECISION",
    # IBD RS Rating(1~99) — 전 종목 대비 가격 모멘텀 백분위(야간 배치).
    "ALTER TABLE universe_snapshot ADD COLUMN IF NOT EXISTS rs_rating SMALLINT",
    # 기술적 추세 종합(0~100) — 종목분석과 동일 4요소 배치 사전계산(스크리너 추세 탭).
    "ALTER TABLE universe_snapshot ADD COLUMN IF NOT EXISTS trend_score DOUBLE PRECISION",
    # 영업이익 손익 4상태(흑자전환/흑자지속/적자전환/적자지속) — 이진 흑자전환의 표시 손실 보완.
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS op_status VARCHAR(8)",
    # 영업이익률 변화(흑자전환 규모, 회사 규모 정규화) — 이진 흑전 가점을 규모 반영으로 대체.
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS op_margin_delta DOUBLE PRECISION",
    # EPS YoY(스냅샷 표시·PEG 산출) — 가치 PEG 축에 사용.
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS eps_yoy DOUBLE PRECISION",
    # 순이익·EBITDA 손익상태 4단계 + 마진 증감(성장 축 — 영업이익과 동일 로직).
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS net_status VARCHAR(8)",
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS net_margin_delta DOUBLE PRECISION",
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS ebitda_status VARCHAR(8)",
    "ALTER TABLE growth_metric ADD COLUMN IF NOT EXISTS ebitda_margin_delta DOUBLE PRECISION",
    # 공시 동기화 깊이(과거 하한) — 얕은 배치 후에도 온디맨드 2년 조회가 실제 fetch 되게(#318).
    "ALTER TABLE disclosure_sync_state ADD COLUMN IF NOT EXISTS synced_from DATE",
    # 캘린더 지난 이벤트 지수영향 방향(positive|negative|neutral) — 프론트 색칠용(#367).
    "ALTER TABLE calendar_event ADD COLUMN IF NOT EXISTS impact_direction VARCHAR(8)",
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
