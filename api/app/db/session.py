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

# 데이터 정합성 정규화(멱등) — 스키마가 아닌 값 보정. init_db 마다 실행되나 조건절이 이미 보정된
# 행을 재매칭하지 않아 안전하다. financials.ebitda·net_debt 는 구 valuation_ingest(원)·신
# report_ingest(억원) 가 같은 컬럼에 써 단위가 혼재했다(검수 결과). 정본 단위 = 억원(revenue 등과 일치).
_DATA_MIGRATIONS = (
    # A) 레거시 valuation_ingest 잔재(원 단위) → 억원. net_debt 보유 행 = 레거시 확정 마커
    #    (신 report_ingest 는 net_debt 를 쓰지 않는다). ebitda·net_debt 동시 정규화.
    "UPDATE financials SET ebitda = ebitda / 1e8 "
    "WHERE net_debt IS NOT NULL AND ebitda IS NOT NULL AND abs(ebitda) > 1e7",
    "UPDATE financials SET net_debt = net_debt / 1e8 "
    "WHERE net_debt IS NOT NULL AND abs(net_debt) > 1e7",
    # A2) net_debt 마커가 없는 잔여 원단위 ebitda(레거시 일부). 같은 종목 다른 행(억원) 중앙값의
    #     1e6 배를 넘으면 원단위 오염 → /1e8. 종목 자기분포 기준이라 절대 임계값 아님(정상행 보호).
    "UPDATE financials f SET ebitda = ebitda / 1e8 "
    "WHERE f.ebitda IS NOT NULL AND abs(f.ebitda) > 1e6 * (SELECT percentile_cont(0.5) "
    "WITHIN GROUP (ORDER BY abs(g.ebitda)) FROM financials g "
    "WHERE g.stock_code = f.stock_code AND g.ebitda IS NOT NULL AND abs(g.ebitda) < 1e7)",
    # B) D&A 오파싱으로 왜곡된 EBITDA(감가상각비를 매출 8배 초과로 잘못 추출) → 무효화.
    #    plausible_depreciation 가드로 재발은 막았고, 기존 오값은 NULL 처리(밸류는 결측 시 우아하게 생략).
    "UPDATE financials SET ebitda = NULL, ev_ebitda = NULL "
    "WHERE ebitda IS NOT NULL AND revenue IS NOT NULL AND revenue > 0 AND ebitda > revenue * 8",
    # C) 소스 오염 행 삭제: DART 원본이 ~1e6 뻥튀기된 분기(per/pbr/psr 0 반올림·bps 조 단위가 지문).
    #    revenue 가 같은 종목 **전체 중앙값**의 1e4 배를 넘으면 원본 오염(억원 규모로 불가능). 대형주도
    #    자기 중앙값 대비 판단하므로 정상 데이터는 보호된다(절대 임계값이 아님).
    "DELETE FROM financials f WHERE f.revenue IS NOT NULL AND f.revenue > 0 "
    "AND f.revenue > 1e4 * (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY g.revenue) "
    "FROM financials g WHERE g.stock_code = f.stock_code AND g.revenue > 0)",
    # D) ReportFinancial 파싱 깨진 가비지 행(1e15 초과 = 경 단위, 불가능) 삭제.
    "DELETE FROM report_financials WHERE abs(revenue) >= 1e15 OR abs(equity) >= 1e15",
)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _COLUMN_MIGRATIONS:
            conn.execute(text(stmt))
        for stmt in _DATA_MIGRATIONS:
            conn.execute(text(stmt))


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
