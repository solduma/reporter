"""Admin TUI 용 시스템 상태 조회 — DB 테이블 행수·최신 적재 시점."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Broadcast,
    DailyMarketInfo,
    Disclosure,
    Financial,
    GrowthMetric,
    MarketQuote,
    Peer,
    PriceCandle,
    PriceCandleIntraday,
    Report,
    ReportAnalysis,
    SyncState,
    TradeStat,
    UniverseSnapshot,
)
from app.services import universe_ingest


def table_counts(db: Session) -> dict[str, int]:
    """주요 테이블 행수."""
    return {
        "reports": db.scalar(select(func.count()).select_from(Report)) or 0,
        "report_analysis": db.scalar(select(func.count()).select_from(ReportAnalysis)) or 0,
        "disclosures": db.scalar(select(func.count()).select_from(Disclosure)) or 0,
        "universe_snapshot": db.scalar(select(func.count()).select_from(UniverseSnapshot)) or 0,
        "growth_metric": db.scalar(select(func.count()).select_from(GrowthMetric)) or 0,
    }


def freshness(db: Session) -> dict[str, str]:
    """데이터 신선도 — 최신 적재 시점(문자열)."""
    latest_report = db.scalar(select(func.max(Report.published_date)))
    latest_uni = universe_ingest.latest_snapshot_date(db)
    uni_rows = 0
    if latest_uni:
        uni_rows = db.scalar(
            select(func.count()).select_from(UniverseSnapshot).where(
                UniverseSnapshot.snapshot_date == latest_uni
            )
        ) or 0
    return {
        "latest_report_date": str(latest_report) if latest_report else "—",
        "latest_universe_date": str(latest_uni) if latest_uni else "—",
        "universe_today_rows": str(uni_rows),
    }


@dataclass
class TableStatus:
    name: str
    rows: int
    latest: str  # 최신 업데이트 시각(문자열, 없으면 '—')


# (표시명, 모델, 최신시각 컬럼). 컬럼이 None 이면 행수만.
_DB_TABLES = [
    ("일봉/주월봉", PriceCandle, PriceCandle.bar_date),
    ("30분봉", PriceCandleIntraday, PriceCandleIntraday.bar_ts),
    ("재무", Financial, Financial.updated_at),
    ("동일업종", Peer, Peer.updated_at),
    ("공시", Disclosure, Disclosure.created_at),
    ("리포트", Report, Report.published_date),
    ("유니버스", UniverseSnapshot, UniverseSnapshot.snapshot_date),
    ("성장지표", GrowthMetric, GrowthMetric.updated_at),
    ("시황요약", DailyMarketInfo, DailyMarketInfo.updated_at),
    ("지수·환율", MarketQuote, MarketQuote.ts),
    ("무역통계", TradeStat, TradeStat.updated_at),
    ("브로드캐스트", Broadcast, Broadcast.created_at),
]


def db_status(db: Session) -> list[TableStatus]:
    """주요 테이블별 행수 + 최신 업데이트 시각. 최신 업데이트 내림차순 정렬(신선한 것 위로).

    latest 컬럼이 date/datetime 이 섞여 문자열 비교가 부정확할 수 있어, 정렬은 원본 값을 ISO
    문자열로 통일해 수행한다(값 없음은 맨 아래). 표시 문자열은 기존처럼 분까지 자른다.
    """
    rows_out: list[tuple[str, TableStatus]] = []
    for name, model, ts_col in _DB_TABLES:
        count = db.scalar(select(func.count()).select_from(model)) or 0
        latest = db.scalar(select(func.max(ts_col)))
        latest_str = str(latest)[:16] if latest is not None else "—"
        # 정렬 키: 있으면 ISO 문자열(date/datetime 모두 사전식=시간순 일치), 없으면 빈 문자열(최하).
        sort_key = latest.isoformat() if latest is not None else ""
        rows_out.append((sort_key, TableStatus(name=name, rows=count, latest=latest_str)))
    rows_out.sort(key=lambda x: x[0], reverse=True)
    return [ts for _, ts in rows_out]


def backfill_progress(db: Session) -> tuple[int, int]:
    """10년 백필 (완료 종목 수, 유니버스 총 종목 수)."""
    done = db.scalar(
        select(func.count()).select_from(SyncState).where(SyncState.domain == "backfill_10y")
    ) or 0
    latest_uni = universe_ingest.latest_snapshot_date(db)
    total = 0
    if latest_uni:
        total = db.scalar(
            select(func.count()).select_from(UniverseSnapshot).where(
                UniverseSnapshot.snapshot_date == latest_uni,
                UniverseSnapshot.stock_type == "stock",
            )
        ) or 0
    return done, total


@dataclass
class PreviewRow:
    stock_name: str
    market_cap: int | None
    revenue_yoy: float | None
    momentum_3m: float | None
    coverage_count: int


@dataclass
class PreviewPage:
    rows: list[PreviewRow]
    total: int


# 정렬 옵션 라벨(TUI 순환 선택용) → 정렬식 팩토리. 2차 키는 결정적 페이지네이션용.
PREVIEW_SORTS: dict = {
    "매출YoY↓": lambda U, G: (G.revenue_yoy.desc().nulls_last(), U.stock_code),
    "모멘텀↓": lambda U, G: (U.momentum_3m.desc().nulls_last(), U.stock_code),
    "시총↑": lambda U, G: (U.market_cap.asc(), U.stock_code),
    "시총↓": lambda U, G: (U.market_cap.desc(), U.stock_code),
    "등락률↓": lambda U, G: (U.change_pct.desc().nulls_last(), U.stock_code),
}


def screener_preview(
    db: Session,
    mktcap_max: int = 500_000_000_000,
    sort: str = "매출YoY↓",
    limit: int = 50,
    offset: int = 0,
) -> PreviewPage:
    """스크리너 미리보기 — 시총 상한 이하 스몰캡을 선택 정렬·페이지로 반환한다.

    TUI 용 경량 조회. 라우터 screen() 은 FastAPI Query 기본값에 의존하므로 직접 호출하지
    않고 여기서 조회한다. total 은 페이지네이션 표시용 전체 건수.
    """
    latest = universe_ingest.latest_snapshot_date(db)
    if not latest:
        return PreviewPage(rows=[], total=0)

    conds = (
        UniverseSnapshot.snapshot_date == latest,
        UniverseSnapshot.stock_type == "stock",
        UniverseSnapshot.market_cap.is_not(None),
        UniverseSnapshot.market_cap <= mktcap_max,
        UniverseSnapshot.trading_value > 100_000_000,
        ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
    )
    base = (
        select(UniverseSnapshot, GrowthMetric)
        .outerjoin(GrowthMetric, GrowthMetric.stock_code == UniverseSnapshot.stock_code)
        .where(*conds)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    order = PREVIEW_SORTS.get(sort, PREVIEW_SORTS["매출YoY↓"])(UniverseSnapshot, GrowthMetric)
    rows = db.execute(base.order_by(*order).limit(limit).offset(offset)).all()
    return PreviewPage(
        rows=[
            PreviewRow(
                stock_name=u.stock_name,
                market_cap=u.market_cap,
                revenue_yoy=g.revenue_yoy if g else None,
                momentum_3m=u.momentum_3m,
                coverage_count=0,
            )
            for u, g in rows
        ],
        total=total,
    )
