"""Admin TUI 용 시스템 상태 조회 — DB 테이블 행수·최신 적재 시점."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Disclosure,
    GrowthMetric,
    Report,
    ReportAnalysis,
    UniverseSnapshot,
)


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
    latest_uni = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
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
    latest = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
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
